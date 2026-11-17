"""Dead torrent removal against a fake qBittorrent: off by default, the timer on stalled or metadata torrents, progress or another state resetting it, paused and finished torrents never dead, removal with or without files, a failed removal tried again, and the timers surviving a restart."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18200
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="127.0.0.1", QBITTORRENT_PORT=PORT, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", ALERT_COOLDOWN=0, DISCORD_USER_ID="")
sys.modules["config.settings"] = c
for key in ("DEAD_TORRENT_DAYS", "DEAD_TORRENT_DELETE_FILES"):
    os.environ.pop(key, None)

import modules.torrents.dead_torrent_monitor as DM
from modules.torrents.qbittorrent_service import QBittorrentService
import isolate  # state files go to a temporary folder

DAY = 86400
CLOCK = [1_000_000.0]
DM.DeadTorrentMonitor._now = staticmethod(lambda: CLOCK[0])
TORRENTS = []
DELETES = []
LISTS = []
FAIL = {"delete": False}


def torrent(name, state, progress, number):
    return {"hash": f"{number:040x}", "name": name, "state": state, "progress": progress, "category": "Movies"}


async def login(request): return web.Response(text="Ok.")
async def info(request): LISTS.append(1); return web.json_response(TORRENTS)


async def delete(request):
    form = await request.post()
    if FAIL["delete"]:
        return web.Response(status=500, text="no")
    DELETES.append((form["hashes"], form["deleteFiles"]))
    TORRENTS[:] = [t for t in TORRENTS if t["hash"] != form["hashes"]]
    return web.Response(text="")


class Notes:
    def __init__(s): s.sent = []
    async def warning(s, title, description="", content=None, **k): s.sent.append((title, description, content))


_files = [0]


def fresh():
    _files[0] += 1
    DM.DeadTorrentMonitor.STATE_FILE = isolate.DATA / f"dead-{_files[0]}.json"
    notes = Notes()
    return notes, DM.DeadTorrentMonitor(notes)


async def check(mon):
    QBittorrentService._snapshot = None
    await mon.check()


async def main():
    app = web.Application()
    app.add_routes([web.post("/api/v2/auth/login", login), web.get("/api/v2/torrents/info", info), web.post("/api/v2/torrents/delete", delete)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== off by default ==")
        notes, mon = fresh()
        TORRENTS[:] = [torrent("Stuck", "stalledDL", 0.3, 1)]
        await check(mon); CLOCK[0] += 30 * DAY; await check(mon)
        print("  DEAD_TORRENT_DAYS unset -> requests:", len(LISTS), "| removed:", DELETES)
        print("  enabled():", DM.DeadTorrentMonitor.enabled())

        os.environ["DEAD_TORRENT_DAYS"] = "7"
        print("\n== the timer ==")
        notes, mon = fresh()
        TORRENTS[:] = [torrent("Stuck", "stalledDL", 0.3, 1), torrent("No Metadata", "metaDL", 0.0, 2)]
        await check(mon)
        CLOCK[0] += 6 * DAY; await check(mon)
        print("  after 6 days -> removed:", DELETES, "| left:", [t["name"] for t in TORRENTS])
        CLOCK[0] += 1 * DAY; await check(mon)
        print("  after 7 days -> removed:", [d[0][-2:] for d in DELETES], "with files:", [d[1] for d in DELETES], "| left:", [t["name"] for t in TORRENTS])
        print("  alerts:", [(t) for t, _, _ in notes.sent])
        print("  text:", notes.sent[0][1].replace("\n\n", " / "))

        print("\n== what is not dead ==")
        notes, mon = fresh(); DELETES.clear()
        TORRENTS[:] = [torrent("Grows", "stalledDL", 0.30, 1), torrent("Active", "downloading", 0.5, 2), torrent("Paused", "pausedDL", 0.2, 3),
                       torrent("Stopped", "stoppedDL", 0.2, 4), torrent("Done", "stalledUP", 1.0, 5), torrent("Seeding", "uploading", 1.0, 6),
                       torrent("Queued", "queuedDL", 0.1, 7), torrent("Flips", "stalledDL", 0.4, 8)]
        await check(mon)
        CLOCK[0] += 5 * DAY
        TORRENTS[0]["progress"] = 0.31; TORRENTS[7]["state"] = "downloading"
        await check(mon)
        TORRENTS[7]["state"] = "stalledDL"
        CLOCK[0] += 5 * DAY; await check(mon)
        print("  ten days in -> removed:", [d[0][-2:] for d in DELETES], "| left:", [t["name"] for t in TORRENTS])
        CLOCK[0] += 2 * DAY; await check(mon)
        print("  two days on -> removed:", [d[0][-2:] for d in DELETES], "| left:", [t["name"] for t in TORRENTS])

        print("\n== keeping the files ==")
        os.environ["DEAD_TORRENT_DELETE_FILES"] = "false"
        notes, mon = fresh(); DELETES.clear()
        TORRENTS[:] = [torrent("Stuck", "stalledDL", 0.3, 1)]
        await check(mon); CLOCK[0] += 8 * DAY; await check(mon)
        print("  deleteFiles sent:", [d[1] for d in DELETES], "|", notes.sent[0][1].split("\n\n")[0][-28:])
        del os.environ["DEAD_TORRENT_DELETE_FILES"]

        print("\n== a removal that fails ==")
        notes, mon = fresh(); DELETES.clear()
        TORRENTS[:] = [torrent("Stuck", "stalledDL", 0.3, 1)]
        await check(mon); CLOCK[0] += 8 * DAY
        FAIL["delete"] = True; await check(mon)
        print("  qBittorrent refuses -> alerts:", len(notes.sent), "| still listed:", len(TORRENTS))
        FAIL["delete"] = False; await check(mon)
        print("  next run -> alerts:", len(notes.sent), "| removed:", len(DELETES))

        print("\n== the timer survives a restart ==")
        notes, mon = fresh(); DELETES.clear()
        TORRENTS[:] = [torrent("Stuck", "stalledDL", 0.3, 1)]
        await check(mon); CLOCK[0] += 4 * DAY
        again = DM.DeadTorrentMonitor(notes); CLOCK[0] += 4 * DAY
        await check(again)
        print("  new instance after 8 days -> removed:", len(DELETES), "| alerts:", len(notes.sent))

        print("\n== not configured ==")
        c.settings.QBITTORRENT_HOST = ""
        LISTS.clear(); notes, mon = fresh(); await check(mon)
        print("  no qBittorrent -> requests:", len(LISTS))
    finally:
        await QBittorrentService.shutdown()
        await runner.cleanup()

asyncio.run(main())
