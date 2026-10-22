"""
Ask Minecraft Java Edition servers for their status.

This is the Server List Ping the game's multiplayer menu uses, spoken
with asyncio streams, so no extra library is required. Every packet is
a VarInt length, a VarInt packet ID and the data:

    -> Handshake       0x00  protocol version, host, port, next state 1
    -> Status request  0x00  (no data)
    <- Status response 0x00  JSON: version, players online/max, sample
    -> Ping            0x01  8-byte token
    <- Pong            0x01  the same token; the time taken is the latency

The format follows the mcstatus library (py-mine/mcstatus,
mcstatus/_protocol/java_client.py and io/base_io.py): integers are
big-endian, strings are a VarInt byte length followed by UTF-8.

.env:
    MINECRAFT_SERVERS   comma separated name=host:port entries; the
                        port defaults to 25565. Empty: feature off.
"""

import asyncio
import json
import os
import random
import re
import struct
import time

from utils.logger import logger


DEFAULT_PORT = 25565

# Any recent protocol number works for a status request; the server
# answers with its own version either way. mcstatus sends 47 (1.8).
PROTOCOL_VERSION = 47

# Seconds allowed for one server: connect, status and ping together.
TIMEOUT = 5

# A status response with a server icon is some tens of KiB; anything
# far larger is not a Minecraft server.
MAX_PACKET = 2 * 1024 * 1024

# Servers that hide player names send this ID with a placeholder name
# ("Anonymous Player").
ANONYMOUS_ID = "00000000-0000-0000-0000-000000000000"

# Colour and style codes such as §a or §l in names and versions.
FORMATTING = re.compile("§.")


def encode_varint(value: int) -> bytes:
    """
    Encode a 32-bit signed integer as a VarInt.

    Seven bits per byte, least significant group first; the high bit
    says another byte follows. Negative numbers are sent as their
    32-bit two's complement, so they always take five bytes.
    """
    if not -(1 << 31) <= value < (1 << 31):
        raise ValueError(f"{value} does not fit in a 32-bit VarInt")

    value &= 0xFFFFFFFF
    out = bytearray()

    while True:
        if value & ~0x7F == 0:
            out.append(value)
            return bytes(out)

        out.append(value & 0x7F | 0x80)
        value >>= 7


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode a VarInt at offset. Returns the value and the offset after it."""
    result = 0

    for shift in range(0, 35, 7):
        if offset >= len(data):
            raise ValueError("VarInt cut off")

        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift

        if not byte & 0x80:
            # Back from two's complement to a signed number.
            if result & (1 << 31):
                result -= 1 << 32
            return result, offset

    raise ValueError("VarInt longer than five bytes")


async def _read_varint(reader: asyncio.StreamReader) -> int:
    data = b""

    for _ in range(5):
        byte = await reader.readexactly(1)
        data += byte

        if not byte[0] & 0x80:
            return decode_varint(data)[0]

    raise ValueError("VarInt longer than five bytes")


def _string(text: str) -> bytes:
    data = text.encode("utf-8")
    return encode_varint(len(data)) + data


def _packet(packet_id: int, payload: bytes = b"") -> bytes:
    body = encode_varint(packet_id) + payload
    return encode_varint(len(body)) + body


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one packet. Returns its ID and the data after the ID."""
    length = await _read_varint(reader)

    if not 0 < length <= MAX_PACKET:
        raise ValueError(f"unexpected packet length {length}")

    body = await reader.readexactly(length)
    packet_id, offset = decode_varint(body)
    return packet_id, body[offset:]


def configured() -> bool:
    return bool(servers())


def servers() -> list[dict]:
    """
    The servers in MINECRAFT_SERVERS, in the order given.

    Entries without a name or host, or with a port that is not a
    number, are skipped with a warning.
    """
    result = []

    for entry in os.getenv("MINECRAFT_SERVERS", "").split(","):
        entry = entry.strip()

        if not entry:
            continue

        name, _, address = entry.partition("=")
        name, address = name.strip(), address.strip()
        host, port = address, str(DEFAULT_PORT)

        # host:port, or [IPv6]:port. A bare IPv6 address has more than
        # one colon and no brackets, so it is taken as a host.
        if address.startswith("["):
            host, _, rest = address[1:].partition("]")
            port = rest.lstrip(":") or port
        elif address.count(":") == 1:
            host, port = address.split(":")

        if not name or not host or not port.strip().isdigit():
            logger.warning("[Minecraft] Skipping MINECRAFT_SERVERS entry %r", entry)
            continue

        result.append({"name": name, "host": host.strip(), "port": int(port)})

    return result


def _clean(text) -> str:
    return FORMATTING.sub("", str(text or "")).strip()


async def query(host: str, port: int, timeout: float = TIMEOUT) -> dict:
    """
    Ask one server for its status.

    Returns version, protocol, online, max, players (names from the
    sample, without anonymous entries), complete (whether those names
    are everyone online) and latency in milliseconds. Raises on any
    failure.
    """
    return await asyncio.wait_for(_query(host, port), timeout)


async def _query(host: str, port: int) -> dict:
    reader, writer = await asyncio.open_connection(host, port)

    try:
        handshake = (
            encode_varint(PROTOCOL_VERSION)
            + _string(host)
            + struct.pack(">H", port)
            + encode_varint(1)  # next state: status
        )
        writer.write(_packet(0x00, handshake) + _packet(0x00))
        started = time.perf_counter()
        await writer.drain()

        packet_id, data = await _read_packet(reader)
        status_time = (time.perf_counter() - started) * 1000

        if packet_id != 0x00:
            raise ValueError(f"unexpected packet {packet_id} instead of the status")

        length, offset = decode_varint(data)
        raw = json.loads(data[offset:offset + length].decode("utf-8"))

        # Ping and pong. Some proxies close the connection after the
        # status; the status round trip then serves as the latency.
        latency = status_time

        try:
            token = random.randint(0, (1 << 63) - 1)
            started = time.perf_counter()
            writer.write(_packet(0x01, struct.pack(">q", token)))
            await writer.drain()
            packet_id, data = await _read_packet(reader)

            if packet_id == 0x01 and data[:8] == struct.pack(">q", token):
                latency = (time.perf_counter() - started) * 1000
        except (OSError, asyncio.IncompleteReadError, ValueError) as ex:
            logger.debug("[Minecraft] No pong from %s:%s: %s", host, port, ex)
    finally:
        writer.close()

        try:
            await writer.wait_closed()
        except Exception:
            pass

    version = raw.get("version") or {}
    players = raw.get("players") or {}
    sample = players.get("sample") or []
    online = int(players.get("online") or 0)
    names = [
        _clean(p.get("name"))
        for p in sample
        if isinstance(p, dict) and p.get("id") != ANONYMOUS_ID and _clean(p.get("name"))
    ]

    return {
        "version": _clean(version.get("name")) or "unknown",
        "protocol": version.get("protocol"),
        "online": online,
        "max": int(players.get("max") or 0),
        "players": sorted(set(names), key=str.lower),
        # Vanilla sends at most 12 names, and none when hidden; only a
        # complete list can tell who joined or left.
        "complete": len(set(names)) == online,
        "latency": round(latency),
    }


async def fetch_all() -> list[dict]:
    """
    Every configured server, asked at the same time.

    Each entry has name, host, port and up (bool); an answering server
    also has the fields of query(), one that does not has error.
    """
    entries = servers()
    results = await asyncio.gather(
        *(query(s["host"], s["port"]) for s in entries),
        return_exceptions=True,
    )
    out = []

    for server, result in zip(entries, results):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result

            error = str(result) or type(result).__name__
            logger.info("[Minecraft] %s not answering: %s", server["name"], error)
            out.append({**server, "up": False, "error": error})
        else:
            out.append({**server, "up": True, **result})

    return out
