"""Minecraft Server List Ping against local fake servers (asyncio): VarInts, the handshake, status and ping, the monitor's join and down alerts, and /minecraft."""
import asyncio
import json
import logging
import os
import struct
import sys
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
SURVIVAL, LOBBY, CREATIVE = 18150, 18151, 18152   # 18152: nothing listens there
os.environ.update({
    "MINECRAFT_SERVERS": f"Survival=127.0.0.1:{SURVIVAL}, Lobby=127.0.0.1:{LOBBY},Creative=127.0.0.1:{CREATIVE}",
    "MINECRAFT_WATCH": "creative, Lobby",
})
os.environ.pop("MINECRAFT_JOIN_ALERTS", None)

import modules.minecraft.minecraft_service as MS
import modules.minecraft.minecraft_monitor as MM
import isolate  # state files go to a temporary folder
MM.MinecraftMonitor.STATE_FILE = isolate.DATA / "minecraft.json"

ZERO = "00000000-0000-0000-0000-000000000000"
PLAYERS = {SURVIVAL: ["Steve", "Alex_99"], LOBBY: []}
VERSION = {SURVIVAL: "Paper 1.21.1", LOBBY: "§aVelocity 3.4"}
HANDSHAKES = []
PINGS = []
DOWN = set()


def packet(packet_id, payload=b""):
    body = MS.encode_varint(packet_id) + payload
    return MS.encode_varint(len(body)) + body


async def read(reader):
    length = await MS._read_varint(reader)
    body = await reader.readexactly(length)
    packet_id, offset = MS.decode_varint(body)
    return packet_id, body[offset:]


def handler(port):
    async def handle(reader, writer):
        try:
            if port in DOWN:
                return
            packet_id, data = await read(reader)
            protocol, i = MS.decode_varint(data)
            size, i = MS.decode_varint(data, i)
            host = data[i:i + size].decode(); i += size
            (to_port,) = struct.unpack(">H", data[i:i + 2]); i += 2
            state, i = MS.decode_varint(data, i)
            HANDSHAKES.append((packet_id, protocol, host, to_port, state))
            packet_id, data = await read(reader)
            assert (packet_id, data) == (0, b""), "expected an empty status request"
            names = PLAYERS[port]
            sample = [{"name": n, "id": f"id-{n}"} for n in names]
            if port == SURVIVAL and len(names) > 3:      # like vanilla: a few names, one hidden
                sample = sample[:3] + [{"name": "Anonymous Player", "id": ZERO}]
            text = json.dumps({"version": {"name": VERSION[port], "protocol": 767},
                               "players": {"max": 20, "online": len(names), **({"sample": sample} if sample else {})},
                               "description": {"text": "A Minecraft Server"}, "favicon": "data:image/png;base64," + "A" * 20000})
            writer.write(packet(0, MS._string(text))); await writer.drain()
            if port == LOBBY:                           # a proxy that closes after the status
                return
            packet_id, data = await read(reader)
            PINGS.append((port, packet_id, len(data)))
            writer.write(packet(1, data)); await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
    return handle


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title))
        return send


async def main():
    print("== VarInt ==")
    for value in (0, 1, 127, 128, 255, 25565, 2097151, 2147483647, -1, -2147483648):
        data = MS.encode_varint(value)
        back = MS.decode_varint(data)
        print(f"  {value:>11} -> {data.hex(' '):15} -> {back[0]} ({'ok' if back == (value, len(data)) else 'WRONG'})")
    for bad in (b"\x80\x80", b"\xff\xff\xff\xff\xff\x01"):
        try:
            MS.decode_varint(bad); print("  no error for", bad.hex())
        except ValueError as ex:
            print(f"  {bad.hex():12} -> ValueError: {ex}")
    try:
        MS.encode_varint(1 << 31); print("  2**31 encoded")
    except ValueError:
        print("  2**31 -> ValueError")

    print("\n== MINECRAFT_SERVERS ==")
    saved = os.environ["MINECRAFT_SERVERS"]
    os.environ["MINECRAFT_SERVERS"] = "Survival=mc.lan, Modded = 10.0.0.5:25570,[::1]:x,=host,v6=[fd00::1]:25566,Broken=host:abc"
    print("  ", [(s["name"], s["host"], s["port"]) for s in MS.servers()])
    os.environ["MINECRAFT_SERVERS"] = ""
    print("   empty -> configured:", MS.configured())
    os.environ["MINECRAFT_SERVERS"] = saved

    servers = [await asyncio.start_server(handler(p), "127.0.0.1", p) for p in (SURVIVAL, LOBBY)]
    try:
        print("\n== status ==")
        for s in await MS.fetch_all():
            shown = {k: s[k] for k in ("version", "protocol", "online", "max", "players", "complete")} if s["up"] else s["error"][:40]
            print(f"  {s['name']:9} up={s['up']} {shown}")
            if s["up"]:
                print(f"  {s['name']:9} latency is a number: {isinstance(s['latency'], int) and s['latency'] >= 0}")
        print("  handshakes:", sorted(set(HANDSHAKES)))
        print("  pings:", PINGS)

        print("\n== monitor, join alerts off ==")
        notes = Notes(); mon = MM.MinecraftMonitor(notes)
        await mon.check()
        PLAYERS[SURVIVAL] = ["Steve", "Notch"]
        await mon.check()
        print("  alerts:", notes.sent)

        print("\n== monitor, join alerts on ==")
        os.environ["MINECRAFT_JOIN_ALERTS"] = "true"
        PLAYERS[SURVIVAL] = ["Steve", "Herobrine"]; PLAYERS[LOBBY] = ["Jeb_"]
        await mon.check()
        for level, title in notes.sent:
            print(f"  {level:9} {title}")
        notes.sent.clear()
        PLAYERS[SURVIVAL] = ["Steve", "Herobrine", "A", "B", "C"]   # only 3 of 5 names sent
        await mon.check()
        PLAYERS[SURVIVAL] = ["Steve"]
        await mon.check()
        print("  incomplete list, then complete again ->", notes.sent)

        print("\n== watched servers going down ==")
        notes.sent.clear()
        await mon.check(); print("  check 1 (Creative unreachable since the start):", notes.sent)
        DOWN.add(LOBBY)
        await mon.check(); print("  Lobby fails once ->", notes.sent)
        await mon.check()
        await mon.check()
        DOWN.discard(LOBBY)
        await mon.check()
        await mon.check()
        for level, title in notes.sent:
            print(f"  {level:9} {title}")
        print("  Survival never alerted down:", not any("Survival" in t for _, t in notes.sent))
        os.environ["MINECRAFT_WATCH"] = ""
        notes.sent.clear(); fresh = Notes()
        MM.MinecraftMonitor.STATE_FILE = isolate.DATA / "minecraft-fresh.json"
        quiet = MM.MinecraftMonitor(fresh)
        for _ in range(3):
            await quiet.check()
        print("  MINECRAFT_WATCH empty, Creative down 3 checks ->", fresh.sent)

        print("\n== /minecraft ==")
        import modules.minecraft.commands.minecraft as MC
        sent = []
        async def defer(**k): sent.append(("defer", k.get("ephemeral")))
        async def send(*a, **k): sent.append(k.get("embed") or a[0])
        interaction = types.SimpleNamespace(
            user=types.SimpleNamespace(display_name="t", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png")),
            response=types.SimpleNamespace(defer=defer), followup=types.SimpleNamespace(send=send))
        PLAYERS[SURVIVAL] = ["Steve", "Alex_99", "A", "B", "C"]
        await MC.Minecraft(None).minecraft.callback(MC.Minecraft(None), interaction)
        print("  deferred privately:", sent[0] == ("defer", True))
        print("  title:", sent[-1].title)
        for line in sent[-1].description.split("\n"):
            # latency differs per run
            print("  |", line.rsplit(" · ", 1)[0] + " · N ms" if " ms" in line else line)
    finally:
        for s in servers:
            s.close(); await s.wait_closed()

    os.environ["MINECRAFT_SERVERS"] = ""
    HANDSHAKES.clear()
    await mon.check()
    print("\nnot configured -> requests:", HANDSHAKES)

asyncio.run(main())
