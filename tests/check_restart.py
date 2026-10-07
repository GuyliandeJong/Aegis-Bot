"""Buttons after a restart, the shared torrent list, state pruning, GiB sizes, and the startup service check."""
import asyncio
import json
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18111
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="127.0.0.1", QBITTORRENT_PORT=PORT, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", IDRAC_HOST="127.0.0.1:1",
                                   IDRAC_USERNAME="u", IDRAC_PASSWORD="p", DISCORD_USER_ID="")
sys.modules["config.settings"] = c
os.environ.update({"JACKETT_URL": f"http://127.0.0.1:{PORT}/jackett", "JACKETT_API_KEY": "k",
                   "JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "good", "JELLYFIN_1_NAME": "Home",
                   "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "bad", "JELLYFIN_2_NAME": "Friend"})

import discord
from utils import open_messages as OM
from utils.formatting import format_bytes
from services.qbittorrent_service import QBittorrentService
from services import startup_check
from monitors.torrent_monitor import TorrentMonitor
from monitors.torrent_health_monitor import TorrentHealthMonitor
import isolate  # state files go to a temporary folder


def stored():
    return json.loads(OM.STATE_FILE.read_text()) if OM.STATE_FILE.exists() else {}


def msg(message_id, ephemeral=False):
    return types.SimpleNamespace(id=message_id, channel=types.SimpleNamespace(id=10),
                                 flags=types.SimpleNamespace(ephemeral=ephemeral))


class V(OM.ExpiringView):
    pass


class FakeChannel:
    def __init__(s): s.edits = []
    def get_partial_message(s, message_id):
        channel = s
        class P:
            async def edit(p, **k):
                if message_id == 404:
                    raise discord.NotFound(types.SimpleNamespace(status=404, reason="x"), "gone")
                channel.edits.append((message_id, k.get("content"), k.get("view")))
        return P()


async def check_buttons():
    print("== buttons after a restart ==")
    v = V(timeout=60); v.message = msg(1)
    e = V(timeout=60); e.message = msg(2, ephemeral=True)
    t = V(timeout=60); t.message = msg(5)
    print("recorded:", sorted(stored()))
    t._dispatch_timeout()  # what discord.py does when the timeout passes
    await asyncio.sleep(0.05)
    print("after view 5 timed out:", sorted(stored()))

    s = V(timeout=60); s.message = msg(3)
    OM.remember(msg(3), note="download note", owner="transfer")
    s.stop(); await asyncio.sleep(0.05)
    print("download took over message 3; owner after view stopped:", stored().get("3", {}).get("owner"))

    OM.remember(msg(404))
    channel = FakeChannel()
    bot = types.SimpleNamespace(get_channel=lambda cid: channel)
    await OM.expire_all(bot)
    for edit in channel.edits:
        print("edited at startup:", edit)
    print("left after startup:", sorted(stored()))
    v.stop(); await asyncio.sleep(0)


class FakeQB(QBittorrentService):
    calls = 0
    async def get_torrents(self, category=None, filter_name=None):
        FakeQB.calls += 1
        return [{"hash": h, "name": h, "state": "uploading", "progress": 1.0} for h in FakeQB.hashes]


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


async def check_torrents():
    print("== torrent monitors ==")
    import tempfile
    from pathlib import Path
    folder = Path(tempfile.mkdtemp())
    TorrentMonitor.STATE_FILE = folder / "tm.json"
    TorrentHealthMonitor.STATE_FILE = folder / "th.json"
    FakeQB.hashes = ["aaa", "bbb", "ccc"]
    tm = TorrentMonitor(Notes()); th = TorrentHealthMonitor(Notes())
    tm.service = FakeQB(); th.service = FakeQB()
    await asyncio.gather(tm.check(), th.check())
    print("qBittorrent list requests for both monitors:", FakeQB.calls)
    FakeQB.hashes = ["aaa"]
    QBittorrentService._snapshot = None
    await tm.check()
    print("TorrentMonitor state after two were removed:", sorted(tm.state))
    print("saved file:", sorted(json.loads(TorrentMonitor.STATE_FILE.read_text())))


def check_units():
    print("== sizes ==")
    for value in (512, 1536, 5 * 1024 ** 3, 3 * 1024 ** 4):
        print(f"{value} -> {format_bytes(value)} | completion alert {TorrentMonitor.format_bytes(value)}")


async def check_startup():
    print("== startup check ==")
    async def version(request): return web.Response(text="v5.0.0")
    async def login(request): return web.Response(text="Ok.")
    async def caps(request):
        return web.Response(status=200 if request.query.get("apikey") == "k" else 401, text="<caps/>")
    async def info(request):
        ok = 'Token="good"' in request.headers.get("Authorization", "")
        return web.json_response({}, status=200 if ok else 401)
    app = web.Application()
    app.add_routes([web.get("/api/v2/app/version", version), web.post("/api/v2/auth/login", login),
                    web.get("/jackett/api/v2.0/indexers/all/results/torznab/api", caps),
                    web.get("/home/System/Info", info), web.get("/friend/System/Info", info)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    notes = Notes()
    try:
        failures = await startup_check.run(notes)
    finally:
        await QBittorrentService.shutdown(); await runner.cleanup()
    print("failures:", failures)
    for note in notes.sent:
        print("alert:", note[0], "|", note[1]); print(note[2])


async def main():
    await check_buttons()
    await check_torrents()
    check_units()
    await check_startup()

asyncio.run(main())
