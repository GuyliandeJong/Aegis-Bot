"""Missing seasons + subtitles, with real discord.py and aiohttp against a local fake Jellyfin pair."""
import asyncio
import logging
import os
import shutil
import sys
import tempfile
import traceback
import types
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
ROOT = Path(tempfile.mkdtemp(prefix="aegis-ss-"))
SERIES, MOVIES = ROOT / "Series", ROOT / "Movies"
(SERIES / "Gravity Falls (2012)" / "Season 01").mkdir(parents=True); MOVIES.mkdir()
PORT = 18098
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home",
                   "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "k", "JELLYFIN_2_NAME": "Friend",
                   "JELLYFIN_SERIES_DIR": str(SERIES), "JELLYFIN_MOVIES_DIR": str(MOVIES)})


def sub(lang, external=False, index=2, text=True):
    return {"Type": "Subtitle", "Language": lang, "IsExternal": external, "IsTextSubtitleStream": text, "Index": index}


VIDEO = {"Type": "Video"}
FRIEND_ITEMS = [
    {"Name": "Gravity Falls", "ProductionYear": 2012, "Id": "gf", "Type": "Series", "Path": "/tv/Gravity Falls"},
    {"Name": "Arrival", "ProductionYear": 2016, "Id": "arr", "Type": "Movie", "Path": "/m/Arrival.2016.mkv", "MediaStreams": [VIDEO, sub("eng")]},
    {"Name": "Amelie", "ProductionYear": 2001, "Id": "ame", "Type": "Movie", "Path": "/m/Amelie.2001.mkv",
     "MediaStreams": [VIDEO, sub("fre"), sub("dut")]},
    {"Name": "Parasite", "ProductionYear": 2019, "Id": "par", "Type": "Movie", "Path": "/m/Parasite.2019.mkv",
     "MediaSources": [{"Id": "par-src"}], "MediaStreams": [VIDEO, sub("eng", external=True, index=3), sub("kor")]},
]
HOME_ITEMS = [{"Name": "Gravity Falls", "ProductionYear": 2012, "Id": "hgf", "Type": "Series", "Path": "/data/tv/Gravity Falls (2012)"}]
SEASONS = {"gf": [{"Id": "s1", "Name": "Season 1", "IndexNumber": 1}, {"Id": "s2", "Name": "Season 2", "IndexNumber": 2}],
           "hgf": [{"Id": "h1", "Name": "Season 1", "IndexNumber": 1}]}
EPISODES = [{"Id": "e1", "Path": "/tv/GF/Season 02/GF.S02E01.mkv", "MediaStreams": [VIDEO, sub("eng")]},
            {"Id": "e2", "Path": "/tv/GF/Season 02/GF.S02E02.mkv", "MediaStreams": [VIDEO, sub("dut")]},
            {"Id": "e3", "Path": "/tv/GF/Season 02/GF.S02E03.mkv", "MediaStreams": [VIDEO, sub("eng", external=True, index=4)]}]
REQUESTS = []


def search(items):
    async def handler(request):
        term = request.query["SearchTerm"].lower(); kind = request.query["IncludeItemTypes"]
        return web.json_response({"Items": [i for i in items if term in i["Name"].lower() and i["Type"] == kind]})
    return handler


async def seasons(request):
    REQUESTS.append(("seasons", request.match_info["id"], request.query.get("IsMissing")))
    return web.json_response({"Items": SEASONS[request.match_info["id"]]})


async def episodes(request):
    assert request.query["SeasonId"] == "s2", request.query
    return web.json_response({"Items": EPISODES})


async def download(request):
    return web.Response(body=b"V" * 100_000)


async def subtitle(request):
    REQUESTS.append(("subtitle", request.path))
    return web.Response(text="1\n00:00:01,000 --> 00:00:02,000\nHello\n")


async def refresh(request):
    return web.Response(status=204)

import discord
import commands.torrent.search as SM
import commands.torrent.jellyfin_download as JD
import isolate  # state files go to a temporary folder
JD.JellyfinTransfer.UPDATE_INTERVAL = 0.05


class Message:
    id = 1
    def __init__(s): s.edits = []; s.sent = []; s.channel = types.SimpleNamespace(send=s._send, fetch_message=s._fetch)
    async def edit(s, **k): s.edits.append(k)
    async def _send(s, text, **k): s.sent.append(text)
    async def _fetch(s, mid): return s


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def _r(s, kind, **k):
        assert not s.done, "responded twice"; s.done = True; s.owner.log.append((kind, k))
        for v in k.values():
            if isinstance(v, discord.ui.View): v.to_components()
    async def defer(s, **k): await s._r("defer")
    async def edit_message(s, **k): await s._r("edit_message", **k)
    async def send_message(s, text=None, **k): await s._r("send_message", text=text)


class Inter:
    def __init__(s, message):
        s.log = []; s.message = message; s.response = Response(s); s.channel = message.channel
        s.user = types.SimpleNamespace(id=1, mention="<@1>")
    async def edit_original_response(s, **k):
        assert s.response.done; s.log.append(("edit_original", k))
        if isinstance(k.get("view"), discord.ui.View): k["view"].to_components()
    async def original_response(s): return s.message


class Cog(SM.TorrentSearchMixin):
    pass


async def do_search(query, category):
    msg = Message(); i = Inter(msg)
    await Cog()._search(i, query=query, category=category)
    return msg, i.log[-1][1].get("embed"), i.log[-1][1].get("view")


async def wait_idle():
    while not JD.is_idle(): await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)


def show(embed):
    print(f"  [{embed.title}]")
    for line in embed.description.splitlines():
        if line: print("    " + line.replace(f"http://127.0.0.1:{PORT}", "http://srv"))


async def main():
    app = web.Application()
    app.add_routes([web.get("/home/Items", search(HOME_ITEMS)), web.get("/friend/Items", search(FRIEND_ITEMS)),
                    web.get("/home/Shows/{id}/Seasons", seasons), web.get("/friend/Shows/{id}/Seasons", seasons),
                    web.get("/friend/Shows/{id}/Episodes", episodes), web.get("/friend/Items/{id}/Download", download),
                    web.get("/friend/Videos/{id}/{src}/Subtitles/{index}/Stream.srt", subtitle),
                    web.post("/home/Library/Refresh", refresh)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("1. Series: Home has season 1, Friend has seasons 1 and 2")
        msg, embed, view = await do_search("Gravity Falls", "Series")
        show(embed)
        print("  buttons:", [c.label for c in view.children], "| IsMissing sent:", {r[2] for r in REQUESTS if r[0] == "seasons"})
        i = Inter(msg); await view.download_button.callback(i)
        picker = i.log[-1][1]["view"]
        print("  picker offers:", [o.label for o in picker.select.options])
        picker.select._values = ["s2"]
        await picker.select.callback(Inter(msg)); await wait_idle()
        show(msg.edits[-1]["embed"])

        print("\n2. Movies: subtitle information in the availability message")
        for title in ("Arrival", "Amelie", "Parasite"):
            msg, embed, view = await do_search(title, "Movies")
            print("  " + title + ": " + [l for l in embed.description.splitlines() if l.startswith("Subtitles")][0])

        print("\n3. Movie with a separate English subtitle file")
        i = Inter(msg); await view.download_button.callback(i); await wait_idle()
        show(msg.edits[-1]["embed"])
        print("  subtitle requests:", [r[1] for r in REQUESTS if r[0] == "subtitle"])

        print("\n4. Searching Gravity Falls again: nothing missing any more")
        SEASONS["hgf"].append({"Id": "h2", "Name": "Season 2", "IndexNumber": 2})
        msg, embed, view = await do_search("Gravity Falls", "Series")
        print(f"  [{embed.title}] view={type(view).__name__}")

        print("\nfiles on disk:")
        for p in sorted(ROOT.rglob("*")):
            if p.is_file(): print("  " + str(p.relative_to(ROOT)).replace(os.sep, "/"))
    except Exception:
        print("EXCEPTION:"); traceback.print_exc()
    finally:
        await runner.cleanup(); shutil.rmtree(ROOT, ignore_errors=True)

asyncio.run(main())
