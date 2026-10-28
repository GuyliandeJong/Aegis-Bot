"""Cam and telesync releases are never offered for movies (kept for PC), a word of the search is never counted as one, a search with only such releases says so and offers the watchlist, every result list has a button to watch for a better release, and the watchlist ignores releases the person already saw."""
import asyncio
import logging
import os
import sys
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
    del os.environ[key]
os.environ.update({"JACKETT_URL": "http://127.0.0.1:1", "JACKETT_API_KEY": "k"})

import discord
from modules.torrents import watchlist as W
import modules.torrents.torrent.search as SM
from modules.torrents.torrent.formatting import TorrentFormattingMixin
import modules.torrents.torrent.watchlist as WV
import modules.torrents.watchlist_monitor as WM
import isolate  # state files go to a temporary folder
W.STATE_FILE = isolate.DATA / "watchlist.json"
WM.WatchlistMonitor.STATE_FILE = isolate.DATA / "watchlist_monitor.json"
SM.TorrentSearchMixin.PREFERENCES_FILE = Path(__file__).resolve().parent.parent / "config" / "torrent_preferences.json"


def release(title, number, indexer="1337x", seeders=50):
    return {"title": title, "magnet": f"magnet:?xt=urn:btih:{number:040x}", "indexer": indexer, "seeders": seeders,
            "leechers": 1, "grabs": 0, "size": 5_000_000_000, "info_hash": f"{number:040x}", "uploader": ""}


class Cog(TorrentFormattingMixin, SM.TorrentSearchMixin, WV.TorrentWatchlistMixin):
    raw = []

    async def _check_jellyfin(self, query, category): return []
    async def _run_search(self, service, query): return ([dict(r) for r in Cog.raw], {})


class Message:
    def __init__(s): s.id = 1; s.flags = types.SimpleNamespace(ephemeral=False); s.channel = types.SimpleNamespace(id=10)
    async def edit(s, **k): pass


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def _r(s, kind, **k):
        s.done = True; s.owner.log.append((kind, k))
        for v in k.values():
            if isinstance(v, discord.ui.View): v.to_components()
    async def defer(s, **k): await s._r("defer", **k)
    async def edit_message(s, **k): await s._r("edit_message", **k)
    async def send_message(s, text=None, **k): await s._r("send_message", text=text, **k)


class Inter:
    def __init__(s, uid=1):
        s.log = []; s.message = Message(); s.response = Response(s); s.channel = types.SimpleNamespace(id=55)
        s.user = types.SimpleNamespace(id=uid, mention=f"<@{uid}>")
    async def edit_original_response(s, **k):
        s.log.append(("edit_original", k))
        if isinstance(k.get("view"), discord.ui.View): k["view"].to_components()
        return s.message
    async def original_response(s): return s.message


def last(i): return i.log[-1][1]


async def titles(query, category, results):
    Cog.raw = results
    ranked, _errors, _query, _fallback = await Cog()._find_torrents(query, category)
    return [r["title"] for r in ranked], getattr(ranked, "hidden", None)


async def filtering():
    print("== which releases are offered ==")
    mixed = lambda: [
        release("Resident Evil (2026) camrip.1080p ENG [OnlyFlix]", 1),
        release("Resident Evil 2026 HDCAM x264", 2),
        release("Resident Evil 2026 HDTS 720p", 3),
        release("Resident Evil 2026 HDTC", 4),
        release("Resident Evil 2026 TELESYNC", 5),
        release("Resident Evil 2026 1080p WEB-DL x265", 6),
        release("Resident Evil 2026 720p WEBRip", 7),
    ]
    shown, hidden = await titles("Resident Evil 2026", "Movies", mixed())
    print("  Movies :", shown, "| hidden:", hidden)
    shown, hidden = await titles("Resident Evil 2026", "PC", mixed())
    print("  PC     :", len(shown), "kept | hidden:", hidden)
    shown, hidden = await titles("Resident Evil 2026", "Series", mixed())
    print("  Series :", len(shown), "kept | hidden:", hidden)

    print("\n== a word of the search is never a cam word ==")
    shown, hidden = await titles("Cam 2018", "Movies", [release("Cam.2018.1080p.WEB-DL", 1), release("Cam 2018 CAMRip", 2), release("Cam (2018) HDCAM", 3)])
    print("  query 'Cam 2018':", shown, "| hidden:", hidden)
    shown, hidden = await titles("Monsters 2010", "Movies", [release("Monsters.2010.1080p.BluRay.x265", 1), release("Tenet 2020 TS", 2)])
    print("  ordinary titles :", shown, "| hidden:", hidden)

    print("\n== the setting ==")
    prefs = Cog()._load_preferences()
    print("  Movies reject_low_quality:", prefs["categories"]["Movies"].get("reject_low_quality"))
    real = SM.TorrentSearchMixin._load_preferences
    SM.TorrentSearchMixin._load_preferences = lambda self: {**prefs, "categories": {**prefs["categories"], "Movies": {**prefs["categories"]["Movies"], "reject_low_quality": False}}}
    shown, hidden = await titles("Resident Evil 2026", "Movies", mixed())
    print("  turned off:", len(shown), "kept | hidden:", hidden)
    SM.TorrentSearchMixin._load_preferences = real
    old = {**prefs, "categories": {**prefs["categories"], "Movies": {k: v for k, v in prefs["categories"]["Movies"].items() if k != "reject_low_quality"}}}
    SM.TorrentSearchMixin._load_preferences = lambda self: old
    shown, hidden = await titles("Resident Evil 2026", "Movies", mixed())
    print("  an older preferences file without the key:", len(shown), "kept | hidden:", hidden)
    SM.TorrentSearchMixin._load_preferences = real


async def only_cams():
    print("\n== a search that finds only cam releases ==")
    Cog.raw = [release("Resident Evil (2026) camrip.1080p", 1), release("Resident Evil 2026 HDCAM", 2), release("Resident Evil 2026 TS", 3)]
    i = Inter()
    await Cog()._search(i, query="Resident Evil 2026", category="Movies")
    k = last(i)
    print("  message:", k["content"].replace("\n", " | "))
    print("  buttons:", [b.label for b in k["view"].children])
    press = Inter(); await k["view"].children[0].callback(press)
    print("  pressed:", last(press)["content"][:62])
    entry = W.all_entries()[-1]
    print("  entry:", entry["query"], entry["category"], "| seen:", entry.get("seen"))
    for e in W.all_entries(): W.remove(e["id"])

    print("\n== nothing found at all keeps its text ==")
    Cog.raw = []
    async def none(self, query): return []
    i = Inter()
    saved = Cog._search_fallback_queries
    Cog._search_fallback_queries = lambda self, q: []
    await Cog()._search(i, query="Obscure Film 1971", category="Movies")
    Cog._search_fallback_queries = saved
    print("  message:", last(i)["content"].splitlines()[0])
    press = Inter(); await last(i)["view"].children[0].callback(press)
    print("  pressed:", last(press)["content"][:34])
    for e in W.all_entries(): W.remove(e["id"])


async def watch_button():
    print("\n== watch for a better release, under the results ==")
    Cog.raw = [release("Resident Evil 2026 1080p WEB-DL x265", 6), release("Resident Evil 2026 720p WEBRip", 7)]
    i = Inter(uid=1)
    await Cog()._search(i, query="Resident Evil 2026", category="Movies")
    k = last(i)
    print("  buttons:", [getattr(b, "label", None) or "dropdown" for b in k["view"].children])
    button = next(b for b in k["view"].children if getattr(b, "label", "") == "Watch for a better release")
    other = Inter(uid=2)
    print("  someone else may press:", await k["view"].interaction_check(other))
    press = Inter(uid=1); await button.callback(press)
    print("  owner presses:", last(press)["text"][:46], "| private:", last(press).get("ephemeral"))
    entry = W.all_entries()[-1]
    print("  entry:", entry["query"], entry["category"], "| seen:", len(entry["seen"]), "keys")
    again = Inter(uid=1); await button.callback(again)
    print("  again:", last(again)["text"])

    print("\n== the watchlist ignores what was already seen ==")
    mon = WM.WatchlistMonitor(types.SimpleNamespace(bot=None))
    searcher = Cog()
    print("  same releases      :", await mon.look_for(searcher, entry))
    Cog.raw = [release("Resident Evil 2026 1080p WEB-DL x265", 6), release("Resident Evil 2026 720p WEBRip", 7), release("Resident Evil 2026 2160p BluRay", 8)]
    print("  one new release    :", await mon.look_for(searcher, entry))
    Cog.raw = [release("Resident Evil 2026 1080p WEB-DL x265", 6), release("Resident Evil 2026 HDCAM", 9)]
    print("  only a new camrip  :", await mon.look_for(searcher, entry))
    old = {k: v for k, v in entry.items() if k != "seen"}
    Cog.raw = [release("Resident Evil 2026 1080p WEB-DL x265", 6), release("Resident Evil 2026 720p WEBRip", 7)]
    print("  entry without seen :", await mon.look_for(searcher, old))
    no_hash = {"title": "Same Title 1080p", "indexer": "rutracker", "info_hash": ""}
    print("  key without a hash :", W.result_key(no_hash))
    for e in W.all_entries(): W.remove(e["id"])


async def main():
    await filtering()
    await only_cams()
    await watch_button()

asyncio.run(main())
