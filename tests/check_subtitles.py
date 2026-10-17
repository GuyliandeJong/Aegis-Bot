"""Subtitles through Jellyfin's providers against a local fake Jellyfin: picking a result, fetch outcomes, the line in "Now in Jellyfin", the daily run, its limits and retry rules, and the weekly summary line."""
import asyncio
import logging
import os
import sys
import time
import types
from datetime import datetime

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18116
for key in [k for k in os.environ if k.startswith(("JELLYFIN_", "SUBTITLE_"))]:
    del os.environ[key]
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}", "JELLYFIN_1_API_KEY": "k", "JELLYFIN_1_NAME": "Home"})

import services.subtitle_fetch as SF
import services.library_followup as LF
import monitors.subtitle_monitor as SM
import monitors.weekly_summary_monitor as WS
from services import activity_log
from services.jellyfin_service import JellyfinService
import isolate  # state files go to a temporary folder
SF.CONFIRM_DELAY = 0
SM.SubtitleMonitor.STATE_FILE = isolate.DATA / "subtitle_monitor_state.json"

# id: name, year, subtitle languages present (None = not scanned yet), search results
MOVIES = {}
REQUESTS = []
FLAGS = {"quota": False, "search_down": False}


def reset():
    MOVIES.clear()
    MOVIES.update({
        "m1": ["Arrival", 2016, [], [
            {"Id": "forced", "ProviderName": "Open Subtitles", "Forced": True, "IsHashMatch": True, "DownloadCount": 900},
            {"Id": "machine", "ProviderName": "Open Subtitles", "MachineTranslated": True, "DownloadCount": 800},
            {"Id": "popular", "ProviderName": "Open Subtitles", "DownloadCount": 500},
            {"Id": "hash", "ProviderName": "Open Subtitles", "IsHashMatch": True, "DownloadCount": 10, "Name": "Arrival.2016.1080p"},
        ]],
        "m2": ["Dune", 2021, ["eng"], []],
        "m3": ["Obscure Film", 1971, [], []],
        "m4": ["Not Scanned", 2024, None, []],
        "m5": ["Heat", 1995, [], [{"Id": "heat", "ProviderName": "Open Subtitles", "DownloadCount": 50}]],
        "m6": ["Ronin", 1998, [], [{"Id": "ronin", "ProviderName": "Open Subtitles", "DownloadCount": 50}]],
    })


def item(movie_id):
    name, year, subs, _ = MOVIES[movie_id]
    out = {"Id": movie_id, "Name": name, "ProductionYear": year, "Type": "Movie"}
    if subs is not None:
        out["MediaStreams"] = [{"Type": "Video", "Width": 1920, "Height": 1080, "Codec": "hevc"}] + [
            {"Type": "Subtitle", "Language": lang, "IsExternal": True, "IsTextSubtitleStream": True, "Index": 2} for lang in subs]
    return out


async def items(request):
    REQUESTS.append(("GET", "/Items", dict(request.query)))
    if request.query.get("Ids"):
        return web.json_response({"Items": [item(i) for i in request.query["Ids"].split(",") if i in MOVIES]})
    return web.json_response({"Items": [item(i) for i in MOVIES]})


async def search(request):
    movie, language = request.match_info["id"], request.match_info["language"]
    REQUESTS.append(("GET", f"search {movie} {language}", {}))
    if FLAGS["search_down"]:
        return web.Response(status=500, text="provider error")
    return web.json_response(MOVIES[movie][3] if movie in MOVIES else [])


async def download(request):
    movie, sub = request.match_info["id"], request.match_info["sub"]
    REQUESTS.append(("POST", f"download {movie} {sub}", {}))
    if not FLAGS["quota"]:
        MOVIES[movie][2].append("eng")
    return web.Response(status=204)  # Jellyfin answers 204 either way


class Notes:
    def __init__(s): s.sent = []
    async def success(s, title, description="", **k): s.sent.append((title, description))


def searches(): return [r[1] for r in REQUESTS if r[1].startswith("search")]
def downloads(): return [r[1] for r in REQUESTS if r[1].startswith("download")]


async def main():
    app = web.Application()
    app.add_routes([web.get("/Items", items), web.get("/Items/{id}/RemoteSearch/Subtitles/{language}", search),
                    web.post("/Items/{id}/RemoteSearch/Subtitles/{sub}", download)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    service = JellyfinService()
    try:
        print("== picking a result ==")
        reset()
        print("  hash match beats popularity, forced/machine skipped:", SF.pick(MOVIES["m1"][3])["Id"])
        hi = [{"Id": "hi", "HearingImpaired": True, "DownloadCount": 900}, {"Id": "plain", "DownloadCount": 100}]
        print("  hearing impaired avoided by default:", SF.pick(hi)["Id"], "| wanted:", SF.pick(hi, True)["Id"])
        print("  nothing usable:", SF.pick([{"Id": "x", "Forced": True}, {"Name": "no id"}]))

        print("\n== one movie ==")
        out = await SF.fetch(service, "Home", "m1")
        print("  missing -> ", out["result"], "| downloaded:", downloads(), "| line:", SF.describe(out))
        print("  language asked:", searches()[-1])
        print("  already has one ->", (await SF.fetch(service, "Home", "m2"))["result"], "| searched:", "search m2 eng" in searches())
        out = await SF.fetch(service, "Home", "m3")
        print("  nothing found ->", out["result"], "|", SF.describe(out))
        FLAGS["quota"] = True
        out = await SF.fetch(service, "Home", "m5")
        print("  accepted but no file ->", out["result"], "|", SF.describe(out))
        FLAGS["quota"] = False; FLAGS["search_down"] = True
        out = await SF.fetch(service, "Home", "m6")
        print("  search failed ->", out["result"], "|", SF.describe(out))
        FLAGS["search_down"] = False
        os.environ["SUBTITLE_LANGUAGE"] = "Dutch"
        await SF.fetch(service, "Home", "m6")
        print("  SUBTITLE_LANGUAGE=Dutch asks for:", searches()[-1])
        del os.environ["SUBTITLE_LANGUAGE"]

        print("\n== after a download completes ==")
        reset()
        line = await LF._subtitles(service, "Home", "Movies", {"item_id": "m1"}, {"title": "Arrival", "year": 2016, "season": None, "episode": None})
        print("  movie:", line)
        print("  series:", await LF._subtitles(service, "Home", "Series", {"item_id": "m1"}, {"title": "x", "year": None, "season": 1, "episode": 1}))
        os.environ["SUBTITLE_AUTO_DOWNLOAD"] = "false"
        print("  turned off:", await LF._subtitles(service, "Home", "Movies", {"item_id": "m3"}, {"title": "x", "year": None, "season": None, "episode": None}))
        del os.environ["SUBTITLE_AUTO_DOWNLOAD"]

        print("\n== daily run ==")
        reset(); REQUESTS.clear(); SF.STATE_FILE.unlink(missing_ok=True)
        counts = await SF.backfill(service, 2)
        print("  limit 2:", {k: v for k, v in counts.items() if v}, "| tried:", [s.split()[1] for s in searches()])
        REQUESTS.clear()
        counts = await SF.backfill(service, 10)
        print("  next day:", {k: v for k, v in counts.items() if v}, "| tried:", [s.split()[1] for s in searches()])
        REQUESTS.clear()
        counts = await SF.backfill(service, 10)
        print("  'nothing found' waits", SF.RETRY_DAYS, "days; tried:", [s.split()[1] for s in searches()], "| skipped unscanned m4:", "search m4 eng" not in searches())
        reset(); REQUESTS.clear(); SF.STATE_FILE.unlink(missing_ok=True); FLAGS["quota"] = True
        counts = await SF.backfill(service, 10)
        print("  refused stops the run:", counts["refused"], "refused, tried:", [s.split()[1] for s in searches()])
        FLAGS["quota"] = False

        print("\n== monitor ==")
        reset(); REQUESTS.clear(); SF.STATE_FILE.unlink(missing_ok=True)
        mon = SM.SubtitleMonitor(Notes())
        mon._now = lambda: datetime(2026, 10, 17, 5, 0); await mon.check()
        print("  before SUBTITLE_HOUR (6):", len(searches()), "searches")
        mon._now = lambda: datetime(2026, 10, 17, 6, 5); await mon.check()
        first = len(searches())
        await mon.check()
        print("  at 06:05:", first, "searches | again the same day:", len(searches()) - first)
        os.environ["SUBTITLE_DAILY_LIMIT"] = "0"
        mon._now = lambda: datetime(2026, 10, 18, 7, 0); await mon.check()
        print("  SUBTITLE_DAILY_LIMIT=0 next day:", len(searches()) - first, "searches")
        del os.environ["SUBTITLE_DAILY_LIMIT"]

        print("\n== weekly summary ==")
        print("  ", WS.WeeklySummaryMonitor._subtitles(time.time() - 3600).replace("\n", " / "))
        print("   empty week:", WS.WeeklySummaryMonitor._subtitles(time.time() + 3600))
    finally:
        await service.close()
        await runner.cleanup()

asyncio.run(main())
