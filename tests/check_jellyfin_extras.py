"""Missing episodes, matching series, quality comparison, and the daily new-content alert with its download picker."""
import asyncio
import logging
import os
import shutil
import sys
import tempfile
import traceback
import types
from datetime import datetime
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
ROOT = Path(tempfile.mkdtemp(prefix="aegis-jx-"))
SERIES, MOVIES = ROOT / "Series", ROOT / "Movies"
(SERIES / "Gravity Falls (2012)" / "Season 1").mkdir(parents=True); MOVIES.mkdir()
PORT = 18112
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home",
                   "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "k", "JELLYFIN_2_NAME": "Friend",
                   "JELLYFIN_SERIES_DIR": str(SERIES), "JELLYFIN_MOVIES_DIR": str(MOVIES), "JELLYFIN_NEW_CONTENT_HOUR": "10"})

import discord
from modules.jellyfin import jellyfin_compare as JC
from modules.jellyfin.jellyfin_service import JellyfinService
import modules.torrents.torrent.search as SM
import modules.jellyfin.jellyfin_download as JD
import modules.jellyfin.new_content_monitor as NM
import isolate  # state files go to a temporary folder
JD.JellyfinTransfer.UPDATE_INTERVAL = 0.05
SM.TorrentSearchMixin.PREFERENCES_FILE = Path(__file__).resolve().parent.parent / "config" / "torrent_preferences.json"
NM.JellyfinNewContentMonitor.STATE_FILE = isolate.DATA / "new_content.json"


def ep(i, series, season, number, path="", end=None):
    e = {"Id": i, "SeriesId": series, "ParentIndexNumber": season, "IndexNumber": number, "SeasonId": f"{series}-s{season}",
         "SeasonName": f"Season {season}", "Path": path or f"/tv/{series}/Season {season:02d}/{series}.S{season:02d}E{number:02d}.mkv"}
    if end: e["IndexNumberEnd"] = end
    return e


def comparison():
    print("== comparing episode lists ==")
    own = [ep("o1", "a", 1, 1), ep("o2", "a", 1, 2), ep("o3", "a", 1, 3), ep("o4", "a", 2, 1), {"Id": "o5", "ParentIndexNumber": 4, "Path": "x"}]
    theirs = [ep("t1", "b", 1, 1), ep("t2", "b", 1, 2), ep("t3", "b", 1, 3), ep("t4", "b", 2, 1), ep("t5", "b", 2, 2), ep("t6", "b", 2, 3, end=4),
              ep("t7", "b", 3, 1), ep("t8", "b", 3, 2), ep("t9", "b", 4, 1)]
    missing = JC.missing_content(own, theirs)
    for entry in missing:
        print(f"season {entry['number']}: complete={entry['complete']} episodes={[e['Id'] for e in entry['episodes']]}")
    print("summary:", JC.describe_missing(missing))
    print("keys:", JC.missing_keys(missing))
    a = [{"Id": "1", "Name": "The Office", "ProductionYear": 2005, "ProviderIds": {"Tvdb": "73244"}},
         {"Id": "2", "Name": "Dune", "ProductionYear": 2000}, {"Id": "3", "Name": "Shogun", "ProductionYear": 2024}]
    b = [{"Id": "x", "Name": "The Office (US)", "ProductionYear": 2005, "ProviderIds": {"Tvdb": "73244"}},
         {"Id": "y", "Name": "Dune", "ProductionYear": 2000}, {"Id": "z", "Name": "Shogun", "ProductionYear": 1980}]
    print("series pairs:", [(o["Id"], t["Id"]) for o, t in JC.match_series(a, b)])
    for w, h in ((1920, 800), (3840, 1600), (1280, 720), (720, 576)):
        print(f"{w}x{h} -> {JellyfinService._resolution_label(w, h)}")


def movie(name, year, mid, width, height, codec, size):
    return {"Name": name, "ProductionYear": year, "Id": mid, "Type": "Movie", "Path": f"/m/{name}.mkv",
            "MediaSources": [{"Id": mid, "Size": size}], "MediaStreams": [{"Type": "Video", "Width": width, "Height": height, "Codec": codec}]}


HOME_SERIES = [{"Name": "Gravity Falls", "ProductionYear": 2012, "Id": "hgf", "Type": "Series", "Path": "/data/tv/Gravity Falls (2012)",
                "ProviderIds": {"Tvdb": "259972"}}]
FRIEND_SERIES = [{"Name": "Gravity Falls", "ProductionYear": 2012, "Id": "fgf", "Type": "Series", "Path": "/tv/Gravity Falls",
                  "ProviderIds": {"Tvdb": "259972"}}]
HOME_EPISODES = [ep("h1", "hgf", 1, 1, "/data/tv/Gravity Falls (2012)/Season 1/a.mkv"), ep("h2", "hgf", 1, 2, "/data/tv/Gravity Falls (2012)/Season 1/b.mkv")]
FRIEND_EPISODES = [ep("f1", "fgf", 1, 1), ep("f2", "fgf", 1, 2), ep("f3", "fgf", 1, 3), ep("f4", "fgf", 2, 1)]
HOME_MOVIES = [movie("Dune", 2021, "hd", 3840, 1600, "hevc", 60 * 1024 ** 3), movie("Arrival", 2016, "ha", 1920, 800, "hevc", 8 * 1024 ** 3)]
FRIEND_MOVIES = [movie("Dune", 2021, "fd", 1920, 800, "hevc", 12 * 1024 ** 3), movie("Arrival", 2016, "fa", 1920, 1080, "h264", 10 * 1024 ** 3)]
LISTED = []


def items(series, episodes, movies, name):
    async def handler(request):
        kind = request.query.get("IncludeItemTypes")
        if "SearchTerm" in request.query:
            term = request.query["SearchTerm"].lower()
            pool = series if kind == "Series" else movies
            return web.json_response({"Items": [i for i in pool if term in i["Name"].lower()]})
        LISTED.append(f"{name}:{kind}")
        return web.json_response({"Items": series if kind == "Series" else episodes})
    return handler


def show_episodes(episodes):
    async def handler(request):
        sid = request.match_info["id"]
        return web.json_response({"Items": [e for e in episodes if e["SeriesId"] == sid]})
    return handler


async def download(request):
    return web.Response(body=b"V" * 50_000)


async def ok(request):
    return web.Response(status=204)


class Message:
    def __init__(s, mid=1): s.id = mid; s.edits = []; s.sent = []; s.flags = types.SimpleNamespace(ephemeral=False); s.channel = types.SimpleNamespace(id=10, send=s._send, fetch_message=s._fetch)
    async def edit(s, **k): s.edits.append(k)
    async def _send(s, text=None, **k): s.sent.append(text); return s
    async def _fetch(s, mid): return s


class Channel:
    def __init__(s): s.posts = []; s.id = 10
    async def send(s, **k): s.posts.append(k); return Message(50)


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def _r(s, kind, **k):
        assert not s.done, "responded twice"; s.done = True; s.owner.log.append((kind, k))
        for v in k.values():
            if isinstance(v, discord.ui.View): v.to_components()
    async def defer(s, **k): await s._r("defer")
    async def edit_message(s, **k): await s._r("edit_message", **k)
    async def send_message(s, text=None, **k): await s._r("send_message", text=text, **k)


class Inter:
    def __init__(s, message, uid=1):
        s.log = []; s.message = message; s.response = Response(s); s.channel = message.channel
        s.user = types.SimpleNamespace(id=uid, mention=f"<@{uid}>")
    async def edit_original_response(s, **k):
        assert s.response.done; s.log.append(("edit_original", k))
        if isinstance(k.get("view"), discord.ui.View): k["view"].to_components()
        return s.message
    async def original_response(s): return s.message


class Cog(SM.TorrentSearchMixin):
    pass


async def wait_idle():
    while not JD.is_idle(): await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)


async def quality():
    print("\n== quality in the availability message ==")
    for title in ("Dune", "Arrival"):
        msg = Message(); i = Inter(msg)
        await Cog()._search(i, query=title, category="Movies")
        embed = i.log[-1][1]["embed"]
        lines = [l for l in embed.description.splitlines() if "·" in l or "fits your" in l]
        print(f"{title}: [{embed.title}]")
        for line in lines: print("   ", line)


async def daily_alert():
    print("\n== daily new-content alert ==")
    channel = Channel()
    bot = types.SimpleNamespace(get_channel=lambda cid: channel, get_cog=lambda name: None)
    monitor = NM.JellyfinNewContentMonitor(types.SimpleNamespace(bot=bot))
    clock = {"now": datetime(2026, 10, 8, 9, 0)}
    monitor._now = lambda: clock["now"]

    await monitor.check(); print("09:00 run   -> posts:", len(channel.posts), "| requests:", len(LISTED))
    clock["now"] = datetime(2026, 10, 8, 10, 5)
    await monitor.check(); print("10:05 run   -> posts:", len(channel.posts), "| requests:", LISTED)
    post = channel.posts[-1]
    print("  [" + post["embed"].title + "]")
    for line in post["embed"].description.splitlines(): print("   ", line)
    view = post["view"]; view.to_components()
    print("  picker:", [(o.label, o.description) for o in view.select.options])
    clock["now"] = datetime(2026, 10, 8, 18, 0)
    await monitor.check(); print("18:00 again -> posts:", len(channel.posts))
    clock["now"] = datetime(2026, 10, 9, 10, 5)
    await monitor.check(); print("next day, nothing new -> posts:", len(channel.posts))
    FRIEND_EPISODES.append(ep("f5", "fgf", 2, 2))
    clock["now"] = datetime(2026, 10, 10, 10, 5)
    await monitor.check(); print("friend added S02E02 (season 2 already reported) -> posts:", len(channel.posts))
    FRIEND_EPISODES.append(ep("f6", "fgf", 3, 1))
    clock["now"] = datetime(2026, 10, 11, 10, 5)
    await monitor.check(); print("friend added season 3 -> posts:", len(channel.posts), "|", channel.posts[-1]["embed"].description.splitlines()[0])

    print("\n  someone picks Gravity Falls in the alert:")
    view = channel.posts[-1]["view"]; view.to_components()
    msg = Message(60); i = Inter(msg, uid=7)
    view.select._values = ["0"]
    await view.select.callback(i)
    embed, source = i.log[-1][1]["embed"], i.log[-1][1]["view"]
    print("  [" + embed.title + "]", "| buttons:", [ch.label for ch in source.children], "| owner:", source.owner_id)
    i2 = Inter(msg, uid=7); await source.download_button.callback(i2)
    picker = i2.log[-1][1]["view"]
    print("  picker:", [o.label for o in picker.select.options])
    picker.select._values = ["0"]
    await picker.select.callback(Inter(msg, uid=7)); await wait_idle()
    final = msg.edits[-1]["embed"]
    print("  [" + final.title + "]", [l for l in final.description.splitlines() if "Copied" in l], "| mention:", msg.sent[-1][:4])
    for post in channel.posts: post["view"].stop()


async def queue():
    print("\n== queue ==")
    JD.JellyfinTransfer.UPDATE_INTERVAL = 10
    transfers = []
    for n in range(3):
        msg = Message(100 + n); i = Inter(msg)
        await i.response.edit_message()
        t = JD.JellyfinTransfer(i, "Friend", f"Movie {n}", [JD.TransferFile(f"q{n}", f"Movie{n}.mkv", MOVIES / f"Movie {n}")])
        await t.start(); transfers.append((t, msg))
    await asyncio.sleep(0.01)
    for t, msg in transfers:
        titles = [e["embed"].title for e in msg.edits]
        print(f"  {t.title}: first shown as {titles[0]!r}")
    await wait_idle()
    for t, msg in transfers:
        print(f"  {t.title}: positions shown {[l for e in msg.edits for l in e['embed'].description.splitlines() if l.startswith('Position')]} -> {msg.edits[-1]['embed'].title}")


async def main():
    comparison()
    app = web.Application()
    app.add_routes([web.get("/home/Items", items(HOME_SERIES, HOME_EPISODES, HOME_MOVIES, "Home")),
                    web.get("/friend/Items", items(FRIEND_SERIES, FRIEND_EPISODES, FRIEND_MOVIES, "Friend")),
                    web.get("/home/Shows/{id}/Episodes", show_episodes(HOME_EPISODES)),
                    web.get("/friend/Shows/{id}/Episodes", show_episodes(FRIEND_EPISODES)),
                    web.get("/friend/Items/{id}/Download", download), web.post("/home/Library/Refresh", ok)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        await quality()
        await daily_alert()
        await queue()
        print("\nfiles on disk:")
        for p in sorted(ROOT.rglob("*")):
            if p.is_file(): print("  " + str(p.relative_to(ROOT)).replace(os.sep, "/"))
    except Exception:
        print("EXCEPTION:"); traceback.print_exc()
    finally:
        await runner.cleanup(); shutil.rmtree(ROOT, ignore_errors=True)

asyncio.run(main())
