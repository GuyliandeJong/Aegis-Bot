"""The weekly summary's "New in Jellyfin" field against two local fake Jellyfin servers: grouping movies and episodes, paging by DateCreated, a server that does not answer, the overflow thread, and turning it off."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="h", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5, QBITTORRENT_USERNAME="u",
                                   QBITTORRENT_PASSWORD="p", ALERT_CHANNEL_ID=10)
sys.modules["config.settings"] = c
PORT = 18145
for key in [k for k in os.environ if k.startswith("JELLYFIN_") or k.startswith("WEEKLY_SUMMARY")]:
    del os.environ[key]
SERVERS = {"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home",
           "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "f", "JELLYFIN_2_NAME": "Friend"}
os.environ.update(SERVERS)

import monitors.weekly_summary_monitor as WS
import services.filesystem_service as FS
import services.qbittorrent_service as QS
from services.jellyfin_service import JellyfinService
import isolate  # state files go to a temporary folder
WS.WeeklySummaryMonitor.STATE_FILE = isolate.DATA / "weekly.json"

NOW = datetime(2026, 10, 18, 10, 30, tzinfo=timezone.utc)
SINCE = (NOW - timedelta(days=7)).timestamp()


def stamp(days_ago, n=0):
    # Jellyfin's own format: UTC, seven decimals.
    return (NOW - timedelta(days=days_ago, seconds=n)).strftime("%Y-%m-%dT%H:%M:%S") + ".1234567Z"


def movie(name, year, days_ago, n=0):
    return {"Type": "Movie", "Name": name, "ProductionYear": year, "DateCreated": stamp(days_ago, n)}


def episode(series, season, number, days_ago):
    return {"Type": "Episode", "Name": f"Episode {number}", "SeriesName": series, "ParentIndexNumber": season,
            "IndexNumber": number, "DateCreated": stamp(days_ago, number)}


LIBRARIES = {
    "home": [movie("Arrival", 2016, 1), movie("Dune", 2021, 2), movie("Heat", None, 3), movie("Old Film", 1990, 20),
             episode("Fallout", 2, 1, 1), episode("Fallout", 2, 2, 1), episode("Fallout", 2, 3, 2),
             episode("The Bear", 3, 5, 4), episode("Fallout", 1, 8, 30),
             {"Type": "Movie", "Name": "No Date", "ProductionYear": 2000}],
    "friend": [movie(f"Friend Film {n:02}", 2000 + n, 1 + n % 6, n) for n in range(10)] + [movie("Ancient", 1950, 40)],
}
DOWN = set()
SEEN = []


def handler(server):
    async def items(request):
        if server in DOWN:
            return web.Response(status=503)
        q = request.query
        SEEN.append((server, q.get("SortBy"), q.get("SortOrder"), q.get("Fields"), q.get("IncludeItemTypes"), q.get("StartIndex"), q.get("Limit")))
        # Items without DateCreated sort last, as an unknown date.
        ordered = sorted(LIBRARIES[server], key=lambda i: i.get("DateCreated") or "", reverse=True)
        start, limit = int(q.get("StartIndex", 0)), int(q.get("Limit", 10 ** 6))
        page = [dict(i) for i in ordered[start:start + limit]]
        if "DateCreated" not in (q.get("Fields") or ""):
            for item in page:
                item.pop("DateCreated", None)
        return web.json_response({"Items": page})
    return items


class Thread:
    def __init__(t): t.posts = []
    async def send(t, text): t.posts.append(text)


def summary_monitor():
    mon = WS.WeeklySummaryMonitor(types.SimpleNamespace())
    mon.overflow = []
    return mon


async def field(mon=None):
    mon = mon or summary_monitor()
    return await mon._new_in_jellyfin(SINCE), mon


def show(label, value):
    print(f"{label}:")
    for line in (value or "").splitlines() or [repr(value)]:
        print("  |", line)


async def main():
    app = web.Application()
    for server in ("home", "friend"):
        app.add_routes([web.get(f"/{server}/Items", handler(server))])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== reading DateCreated ==")
        print("  seven decimals, Z:", JellyfinService.date_created({"DateCreated": "2026-10-15T12:34:56.1234567Z"}) == datetime(2026, 10, 15, 12, 34, 56, tzinfo=timezone.utc).timestamp())
        print("  no zone taken as UTC:", JellyfinService.date_created({"DateCreated": "2026-10-15T12:34:56"}) == datetime(2026, 10, 15, 12, 34, 56, tzinfo=timezone.utc).timestamp())
        print("  missing or garbage:", JellyfinService.date_created({}), JellyfinService.date_created({"DateCreated": "yesterday"}))

        print("\n== both servers answer ==")
        value, mon = await field()
        show("field", value)
        home = [s for s in SEEN if s[0] == "home"]
        print("  asked:", home[0][1:5])
        print("  Home requests:", len(home), "| Friend requests:", len([s for s in SEEN if s[0] == "friend"]), "| overflow:", len(mon.overflow))

        print("\n== field order in the summary ==")
        async def used_bytes(self): return {"/": (1, 2)}
        async def torrents(self, category=None, filter_name=None): return []
        FS.FilesystemService.get_used_bytes = used_bytes
        QS.QBittorrentService.get_torrents = torrents
        names = [name for name, _, _ in await summary_monitor().build(SINCE)]
        print("  after copies:", names[names.index("📥 Copied from Jellyfin") + 1])

        print("\n== Friend does not answer ==")
        DOWN.add("friend")
        value, _ = await field()
        show("field", value)
        DOWN.add("home")
        value, _ = await field()
        show("both down", value)
        DOWN.clear()

        print("\n== a busy week: paging and the thread ==")
        LIBRARIES["friend"] = [movie(f"Imported Film Number {n:03}", 2000 + n % 25, 1 + n % 5, n) for n in range(450)] + [movie("Ancient", 1950, 40)]
        SEEN.clear()
        value, mon = await field()
        show("field", value)
        print("  Friend pages asked:", [(s[5], s[6]) for s in SEEN if s[0] == "friend"])
        thread = Thread()
        class Summary:
            channel = None
            async def create_thread(s, name, auto_archive_duration): return thread
        await mon._post_full_lists(Summary(), datetime(2026, 10, 18))
        text = "\n".join(thread.posts)
        print("  thread messages:", len(thread.posts), "| server headings:", [l for l in text.splitlines() if l.startswith("**Home") or l.startswith("**Friend")],
              "| lines:", text.count("•"), "| longest:", max(len(p) for p in thread.posts) <= 2000)

        print("\n== one server ==")
        for key in [k for k in os.environ if k.startswith("JELLYFIN_2_")]:
            del os.environ[key]
        value, mon = await field()
        show("field", value)
        LIBRARIES["home"] = [movie("Old Film", 1990, 20)]
        value, _ = await field()
        show("nothing new", value)

        print("\n== turned off, or no Jellyfin ==")
        os.environ["WEEKLY_SUMMARY_NEW_CONTENT"] = "false"
        value, _ = await field()
        print("  WEEKLY_SUMMARY_NEW_CONTENT=false ->", value)
        del os.environ["WEEKLY_SUMMARY_NEW_CONTENT"]
        for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
            del os.environ[key]
        value, _ = await field()
        names = [name for name, _, _ in await summary_monitor().build(SINCE)]
        print("  no servers ->", value, "| field in summary:", "🆕 New in Jellyfin" in names)
    finally:
        await runner.cleanup()

asyncio.run(main())
