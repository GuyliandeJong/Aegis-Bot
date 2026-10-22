"""The weekly summary's "Watched this week" field against a local fake Jellyfin with the Playback Reporting plugin: the query sent, totals per user and title, transcoded share, an empty week, the plugin missing, a server down, and the setting turned off."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime

from aiohttp import web

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="h", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5, QBITTORRENT_USERNAME="u",
                                   QBITTORRENT_PASSWORD="p", ALERT_CHANNEL_ID=10)
sys.modules["config.settings"] = c
PORT = 18180
for key in [k for k in os.environ if k.startswith(("JELLYFIN_", "WEEKLY_SUMMARY"))]:
    del os.environ[key]
os.environ.update({
    "JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home",
    "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "f", "JELLYFIN_2_NAME": "Friend",
})

from modules.jellyfin import watch_stats as W
import modules.general.weekly_summary_monitor as WS
import isolate  # state files go to a temporary folder

WS.WeeklySummaryMonitor.STATE_FILE = isolate.DATA / "weekly.json"

# Rows as the plugin returns them: every cell a string, UserId already
# replaced by the user name (ReplaceUserId), column list spelled "colums".
COLUMNS = ["UserName", "ItemType", "ItemName", "PlaybackMethod", "PlayDuration"]
ROWS = [
    ["Alex", "Episode", "Fallout - s02e03 - The Other Player", "DirectPlay", "3600"],
    ["Alex", "Episode", "Fallout - s02e04 - Another", "Transcode (v:h264 a:aac)", "3000"],
    ["Anna", "Movie", "Arrival", "DirectStream", "6900"],
    ["Anna", "Episode", "The Office (US) - s01e01 - Pilot", "na", "1380"],
    ["Alex", "Movie", "Heat", "Transcode (v:direct a:aac)", "10200"],
    ["Bob", "Movie", "Dune", "DirectPlay", "9300"],
    ["Carl", "Movie", "Alien", "DirectPlay", "600"],
    ["Dana", "Movie", "Ronin", "DirectPlay", "300"],
    ["Eve", "Movie", "Alien", "DirectPlay", "120"],
    ["0123456789abcdef0123456789abcdef", "Movie", "Old", "DirectPlay", "60"],
]
MODE = {"home": "ok"}
SEEN = []


async def query(request):
    server = request.match_info["server"]
    data = await request.json()
    SEEN.append((server, request.headers.get("Authorization"), data))
    mode = MODE.get(server, "ok")
    if mode == "missing":
        return web.Response(status=404)
    if mode == "empty":
        return web.json_response({"colums": [], "results": [], "message": "Query executed, no data returned.</br>Number of rows effected : 0"})
    return web.json_response({"colums": COLUMNS, "results": ROWS, "message": ""})


def show(text):
    for line in (text or "None").split("\n"):
        print("  |", line)


async def main():
    app = web.Application()
    app.add_routes([web.post("/{server}/user_usage_stats/submit_custom_query", query)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    since = datetime(2026, 10, 11, 10, 0).timestamp()
    try:
        print("== a normal week ==")
        show(await W.field(since))
        server, auth, body = SEEN[-1]
        print("  asked:", server, "only |", auth, "| ReplaceUserId:", body["ReplaceUserId"])
        print("  query:", body["CustomQueryString"])

        print("\n== episode names ==")
        print("  ", W.title_of("Episode", "Fallout - s02e03 - The Other Player"), "|",
              W.title_of("Episode", "Odd name"), "|", W.title_of("Movie", "Mission - Impossible"))

        print("\n== no transcode information ==")
        text = W.summarize(COLUMNS, [r[:3] + ["na", r[4]] for r in ROWS[:2]])
        print("   Transcoded line:", "Transcoded" in text)

        print("\n== empty week ==")
        MODE["home"] = "empty"
        print("  ", await W.field(since))

        print("\n== in the summary ==")
        MODE["home"] = "ok"
        mon = WS.WeeklySummaryMonitor(None)
        mon.overflow = []
        async def nothing(*a): return "x"
        mon._disks = nothing; mon._torrents = nothing
        names = [name for name, _, _ in await mon.build(since)]
        print("  fields:", names)
        os.environ["WEEKLY_SUMMARY_WATCH_STATS"] = "false"
        names = [name for name, _, _ in await mon.build(since)]
        print("  turned off, watched field present:", "🍿 Watched this week" in names)
        del os.environ["WEEKLY_SUMMARY_WATCH_STATS"]

        print("\n== a removed user ==")
        print("  ", W.summarize(COLUMNS, ROWS[-1:]).split("\n")[1])

        print("\n== plugin missing ==")
        MODE["home"] = "missing"
        print("  ", await W.field(since))

        print("\n== server down ==")
        await runner.cleanup()
        print("  ", await W.field(since))

        for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
            del os.environ[key]
        print("  no Jellyfin configured:", await W.field(since))
    finally:
        await runner.cleanup()


asyncio.run(main())
