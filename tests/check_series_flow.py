"""Series flow with the real discord.py and real aiohttp against a local fake Jellyfin."""
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
ROOT = Path(tempfile.mkdtemp(prefix="aegis-real-"))
(ROOT / "Series").mkdir()
PORT = 18096
os.environ.update({"JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}", "JELLYFIN_2_API_KEY": "k", "JELLYFIN_2_NAME": "jellyfin.example",
                   "JELLYFIN_SERIES_DIR": str(ROOT / "Series")})
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h"})

SID = "a" * 32
EPISODES = {"e1": b"1" * 200_000, "e2": b"2" * 200_000}


async def items(request):
    return web.json_response({"Items": [{"Name": "Gravity Falls", "ProductionYear": 2012, "Id": SID, "Type": "Series", "Path": "/tv/Gravity Falls"}]})


async def home_items(request):
    return web.json_response({"Items": []})


async def home_refresh(request):
    return web.Response(status=204)


async def seasons(request):
    return web.json_response({"Items": [{"Id": "s0", "Name": "Specials", "IndexNumber": 0}, {"Id": "s1", "Name": "Season 1", "IndexNumber": 1}]})


async def episodes(request):
    assert request.query["SeasonId"] == "s1"
    return web.json_response({"Items": [{"Id": e, "Path": f"/tv/Gravity Falls/Season 01/GF.S01{e.upper()}.mkv"} for e in EPISODES]})


async def download(request):
    return web.Response(body=EPISODES[request.match_info["id"]], headers={"Content-Disposition": "attachment"})


import discord
import modules.torrents.torrent.search as SM
import modules.jellyfin.jellyfin_download as JD
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


async def main():
    app = web.Application()
    app.add_routes([web.get("/home/Items", home_items), web.post("/home/Library/Refresh", home_refresh), web.get("/Items", items), web.get("/Shows/{id}/Seasons", seasons), web.get("/Shows/{id}/Episodes", episodes),
                    web.get("/Items/{id}/Download", download)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        msg = Message(); i = Inter(msg)
        await Cog()._search(i, query="Gravity Falls", category="Series")
        embed, view = i.log[-1][1]["embed"], i.log[-1][1]["view"]
        print("search   :", embed.title, "|", type(view).__name__)
        button = view.download_button
        i2 = Inter(msg)
        await button.callback(i2)
        for kind, k in i2.log: print("  step   :", kind, "|", k["embed"].title, "-", k["embed"].description.splitlines()[0], "| view:", type(k.get("view")).__name__)
        picker = i2.log[-1][1].get("view")
        picker.select._values = ["1"]
        i3 = Inter(msg)
        await picker.select.callback(i3)
        while not JD.is_idle(): await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        print("result   :", msg.edits[-1]["embed"].title, "|", [l for l in msg.edits[-1]["embed"].description.splitlines() if l][1:])
        print("on disk  :", sorted(str(p.relative_to(ROOT)).replace(os.sep, "/") for p in ROOT.rglob("*") if p.is_file()))
    except Exception:
        print("EXCEPTION:"); traceback.print_exc()
    finally:
        await runner.cleanup(); shutil.rmtree(ROOT, ignore_errors=True)

asyncio.run(main())
