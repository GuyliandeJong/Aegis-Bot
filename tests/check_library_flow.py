"""Release-name parsing, and the Jellyfin refresh plus "Now in Jellyfin" message after a torrent completes."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(DISCORD_USER_ID="111"); sys.modules["config.settings"] = c
PORT = 18113
for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
    del os.environ[key]
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home"})
# Subtitle fetching has its own check (check_subtitles).
os.environ["SUBTITLE_AUTO_DOWNLOAD"] = "false"

from services.release_name import parse
from services import library_followup as LF
from services import torrent_requesters as TR
import isolate  # state files go to a temporary folder
LF.CHECK_INTERVAL = 0.01
LF.CHECKS = 4
LF.REFRESH_AGAIN_AFTER = 2

NAMES = ["The.Dark.Knight.Rises.2012.1080p.BluRay.DDP5.1.x265.10bit-GalaxyRG265", "Fallout.S02E03.1080p.WEB.h264-ETHEL",
         "Fallout S02 1080p AMZN WEB-DL", "2001.A.Space.Odyssey.1968.2160p.UHD", "Blade Runner 2049 (2017) [1080p]",
         "Shogun.2024.S01E01.1080p", "The Office US Season 3 Complete 720p"]

LIBRARY = {"Movie": [], "Series": [{"Name": "Fallout", "ProductionYear": 2024, "Id": "fo", "Type": "Series"}]}
EPISODES = {"fo": [{"Id": "a", "ParentIndexNumber": 1, "IndexNumber": 1}]}
SEEN = {"refresh": 0, "searches": 0}


async def items(request):
    SEEN["searches"] += 1
    kind = request.query["IncludeItemTypes"]; term = request.query["SearchTerm"].lower()
    # The movie only appears after the third search, as if the scan took a while.
    pool = LIBRARY[kind]
    if kind == "Movie" and SEEN["searches"] >= 3:
        pool = [{"Name": "The Dark Knight Rises", "ProductionYear": 2012, "Id": "dkr", "Type": "Movie"}]
    return web.json_response({"Items": [i for i in pool if term in i["Name"].lower()]})


async def episodes(request):
    return web.json_response({"Items": EPISODES.get(request.match_info["id"], [])})


async def refresh(request):
    SEEN["refresh"] += 1
    return web.Response(status=204)


class Notes:
    def __init__(s): s.sent = []
    async def success(s, title, description, **k): s.sent.append((title, description, k.get("content")))


async def run(name, category, hash_="h1"):
    notes = Notes()
    await LF.follow_up(notes, {"name": name, "category": category, "hash": hash_})
    return notes.sent


async def main():
    print("== release names ==")
    for name in NAMES:
        r = parse(name)
        print(f"  {r['title']!r:24} year={r['year']} season={r['season']} episode={r['episode']}")

    app = web.Application()
    app.add_routes([web.get("/home/Items", items), web.get("/home/Shows/{id}/Episodes", episodes), web.post("/home/Library/Refresh", refresh)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("\n== after a torrent completes ==")
        TR.remember("h1", 222)
        sent = await run(NAMES[0], "Movies")
        print("movie appears after a while ->", sent, "| refreshes:", SEEN["refresh"])

        SEEN["refresh"] = 0
        sent = await run(NAMES[1], "Series", "h2")
        print("episode not there yet       ->", sent, "| refreshes (one forced again):", SEEN["refresh"])

        EPISODES["fo"].append({"Id": "b", "ParentIndexNumber": 2, "IndexNumber": 3})
        sent = await run(NAMES[1], "Series", "h2")
        print("episode there               ->", sent)

        sent = await run(NAMES[2], "Series", "h3")
        print("season pack, S02 present    ->", [s[1] for s in sent])

        sent = await run("Some.Album.2020.FLAC", "Music")
        print("Music category              ->", sent)

        os.environ["JELLYFIN_REFRESH_ON_COMPLETE"] = "false"
        calls = []
        LF.follow_up = lambda *a: calls.append(a)
        LF.start(Notes(), {"name": NAMES[0], "category": "Movies"})
        print("turned off -> follow-up started:", bool(calls))
    finally:
        await runner.cleanup()

asyncio.run(main())
