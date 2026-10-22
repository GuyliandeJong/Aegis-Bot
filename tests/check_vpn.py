"""VPN check against a fake qBittorrent Web API: leak alert with masked addresses, disconnected warning, recovery, older versions, and /torrent status."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18140
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="127.0.0.1", QBITTORRENT_PORT=PORT, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", ALERT_COOLDOWN=0, DISCORD_USER_ID="")
sys.modules["config.settings"] = c
# Inside the bot's container the real .env is loaded.
os.environ.pop("VPN_CHECK", None)

from core.state import state
import modules.torrents.vpn_monitor as VM
from modules.torrents.qbittorrent_service import QBittorrentService
import isolate  # state files go to a temporary folder
VM.VpnMonitor.STATE_FILE = isolate.DATA / "vpn.json"

HOME = "203.0.113.7"
VPN = "185.65.134.80"
# What /transfer/info answers; None leaves the address fields out, as
# qBittorrent before 5.1 does.
QBT = {"v4": VPN, "status": "connected"}
CALLS = []
WAN_LOOKUPS = []


async def login(request):
    resp = web.Response(text="Ok.")
    resp.set_cookie("SID", "abc")
    return resp


async def transfer_info(request):
    CALLS.append(request.path)
    if request.cookies.get("SID") != "abc":
        return web.Response(status=403, text="Forbidden")
    data = {"dl_info_speed": 1024, "up_info_speed": 0, "dl_info_data": 0, "up_info_data": 0,
            "dht_nodes": 300, "connection_status": QBT["status"]}
    if QBT["v4"] is not None:
        data["last_external_address_v4"] = QBT["v4"]
        data["last_external_address_v6"] = ""
    return web.json_response(data)


async def version(request):
    return web.Response(text="v5.1.2")


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


class Logs(logging.Handler):
    def __init__(s): super().__init__(); s.lines = []
    def emit(s, record): s.lines.append((record.levelname, record.getMessage()))


async def fake_get_ip(self):
    WAN_LOOKUPS.append(1)
    return HOME


async def main():
    app = web.Application()
    app.add_routes([web.post("/api/v2/auth/login", login), web.get("/api/v2/transfer/info", transfer_info),
                    web.get("/api/v2/app/version", version)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    VM.WanService.get_ip = fake_get_ip
    state.wan["ip"] = HOME
    try:
        print("== helpers ==")
        print("  mask:", VM.VpnMonitor.mask(HOME), "|", VM.VpnMonitor.mask("2001:db8::1"), "|", VM.VpnMonitor.mask(""))
        for transfer, home in [({"last_external_address_v4": VPN}, HOME), ({"last_external_address_v4": HOME}, HOME),
                               ({"last_external_address_v4": ""}, HOME), ({}, HOME), ({"last_external_address_v4": VPN}, None)]:
            print(f"  verdict {str(transfer):42} home={home!s:12} -> {VM.VpnMonitor.verdict(transfer, home)}")

        print("\n== leak ==")
        notes = Notes(); mon = VM.VpnMonitor(notes)
        await mon.check(); print("  behind the VPN          ->", [t for _, t, _ in notes.sent])
        QBT["v4"] = HOME
        await mon.check(); print("  home address            ->", [(lvl, t) for lvl, t, _ in notes.sent])
        await mon.check(); print("  still home address      ->", len(notes.sent), "alert(s) in total")
        again = VM.VpnMonitor(notes); await again.check()
        print("  after a restart         ->", len(notes.sent), "alert(s) in total")
        QBT["v4"] = ""
        await mon.check(); print("  address not known yet   ->", len(notes.sent), "alert(s) in total")
        QBT["v4"] = VPN
        await mon.check(); print("  VPN address again       ->", [(lvl, t) for lvl, t, _ in notes.sent[1:]])
        text = " ".join(d for _, _, d in notes.sent)
        print("  full address in any alert:", HOME in text or VPN in text, "| masked shown:", "`203.0.x.x`" in text and "`185.65.x.x`" in text)
        print("  critical text:", notes.sent[0][2].replace("\n", " / "))

        print("\n== disconnected ==")
        notes.sent.clear()
        QBT["status"] = "disconnected"
        seen = []
        for _ in range(4):
            await mon.check(); seen.append(len(notes.sent))
        print("  alerts after each of 4 checks:", seen)
        QBT["status"] = "firewalled"
        await mon.check()
        print("  ", [(lvl, t) for lvl, t, _ in notes.sent])
        QBT["status"] = "disconnected"
        await mon.check(); await mon.check(); QBT["status"] = "connected"; await mon.check()
        print("  two disconnected checks, then connected ->", len(notes.sent), "alert(s) in total")

        print("\n== qBittorrent before 5.1 ==")
        logs = Logs(); logging.getLogger("aegis").addHandler(logs); logging.getLogger("aegis").setLevel(logging.INFO)
        notes = Notes(); VM.VpnMonitor.STATE_FILE = isolate.DATA / "vpn-old.json"
        old = VM.VpnMonitor(notes)
        QBT["v4"] = None; QBT["status"] = "disconnected"
        for _ in range(3):
            await old.check()
        info = [line for level, line in logs.lines if level == "INFO" and "external address" in line]
        print("  logged once:", len(info), "|", info[0] if info else "")
        print("  connection still checked ->", [(lvl, t) for lvl, t, _ in notes.sent])
        logging.getLogger("aegis").removeHandler(logs); logging.getLogger("aegis").setLevel(logging.WARNING)

        print("\n== home IP not recorded yet ==")
        state.wan.pop("ip", None)
        notes = Notes(); VM.VpnMonitor.STATE_FILE = isolate.DATA / "vpn-wan.json"
        QBT["v4"] = HOME; QBT["status"] = "connected"
        await VM.VpnMonitor(notes).check()
        print("  WAN lookups:", len(WAN_LOOKUPS), "| alerts:", [(lvl, t) for lvl, t, _ in notes.sent])
        state.wan["ip"] = HOME

        print("\n== /torrent status ==")
        from modules.torrents.torrent.formatting import TorrentFormattingMixin
        from modules.torrents.torrent.status import TorrentStatusMixin

        class Cog(TorrentStatusMixin, TorrentFormattingMixin):
            pass

        sent = []
        async def defer(**k): pass
        async def send(*a, **k): sent.append(k.get("embed"))
        interaction = types.SimpleNamespace(
            user=types.SimpleNamespace(display_name="t", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png")),
            response=types.SimpleNamespace(defer=defer), followup=types.SimpleNamespace(send=send))
        for v4 in (VPN, HOME, None):
            QBT["v4"] = v4
            await Cog()._status(interaction)
            vpn = [f.value for f in sent[-1].fields if f.name == "VPN"]
            print(f"  address {v4!s:14} -> VPN field: {vpn}")
        os.environ["VPN_CHECK"] = "false"
        await Cog()._status(interaction)
        print("  VPN_CHECK=false -> fields:", [f.name for f in sent[-1].fields])

        print("\n== off ==")
        CALLS.clear()
        await mon.check()
        print("  VPN_CHECK=false -> requests:", CALLS)
        del os.environ["VPN_CHECK"]
        c.settings.QBITTORRENT_HOST = ""
        await mon.check()
        print("  qBittorrent not configured -> requests:", CALLS)
    finally:
        await QBittorrentService.shutdown()
        await runner.cleanup()

asyncio.run(main())
