"""Torznab categories are read from a Jackett feed (both forms, repeated, custom numbers, none), a search shows only its own kind of content (games, music and adult releases leave a Movies search; a result with no usable category stays), the setting can turn it off, the hidden count is shown in the footer or, when nothing is left, in the message with the watchlist button, and the watchlist uses the same filter."""
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
from modules.torrents.jackett_service import JackettService
import modules.torrents.torrent.search as SM
import modules.torrents.torrent.watchlist as WV
import modules.torrents.watchlist_monitor as WM
from modules.torrents.torrent.formatting import TorrentFormattingMixin
import isolate  # state files go to a temporary folder
W.STATE_FILE = isolate.DATA / "watchlist.json"
WM.WatchlistMonitor.STATE_FILE = isolate.DATA / "watchlist_monitor.json"
SM.TorrentSearchMixin.PREFERENCES_FILE = Path(__file__).resolve().parent.parent / "config" / "torrent_preferences.json"

NS = 'xmlns:torznab="http://torznab.com/schemas/2015/feed"'


def xml_item(title, number, categories="", plain=""):
    attrs = "".join(f'<torznab:attr name="category" value="{c}"/>' for c in categories.split() if c)
    elements = "".join(f"<category>{c}</category>" for c in plain.split() if c)
    return (f"<item><title>{title}</title><guid>g{number}</guid><size>1</size>{elements}"
            f'<link>magnet:?xt=urn:btih:{number:040x}</link>{attrs}'
            f'<torznab:attr name="seeders" value="{number}"/></item>')


def feed(*items):
    return f'<?xml version="1.0"?><rss {NS}><channel><category>9999</category>{"".join(items)}</channel></rss>'


print("== categories read from a feed ==")
parsed = JackettService.parse_results(feed(
    xml_item("both forms", 1, "2000 2040 100001", "2000 2040 100001"),
    xml_item("attr only, repeated", 2, "4000 4050"),
    xml_item("custom only", 3, "100077"),
    xml_item("none", 4),
    xml_item("junk text", 5, "", "Movies/HD 2040 x"),
), "x")
for r in parsed:
    print(f"  {r['title']:20} -> {r['categories']}")


def release(title, number, categories=None, indexer="1337x"):
    result = {"title": title, "magnet": f"magnet:?xt=urn:btih:{number:040x}", "indexer": indexer, "seeders": 50,
              "leechers": 1, "grabs": 0, "size": 5_000_000_000, "info_hash": f"{number:040x}", "uploader": ""}
    if categories is not None:
        result["categories"] = categories
    return result


class Cog(TorrentFormattingMixin, SM.TorrentSearchMixin, WV.TorrentWatchlistMixin):
    raw = []

    async def _check_jellyfin(self, query, category): return []
    async def _run_search(self, service, query): return ([dict(r) for r in Cog.raw], {})


# The shape of the search for "Resident Evil 2026" that started this.
MIXED = lambda: [
    release("Resident Evil 2026 1080p WEB-DL x265", 1, [2040, 100003]),
    release("Resident Evil 2026 720p WEBRip", 2, [2000]),
    release("Resident Evil 2026 1080p HDRip", 3),
    release("Resident Evil 2026 custom-numbered", 4, [100050]),
    release("Resident Evil Requiem (2026) FULL UNLOCKED", 5, [4050]),
    release("Resident Evil Requiem Original Soundtrack FLAC", 6, [3040], "rutracker"),
    release("Resident Evil Cosplay Clip", 7, [6000]),
    release("Resident Evil 2026 Documentary", 8, [5080]),
    release("Resident Evil 2026 misc", 9, [8010]),
    release("Resident Evil 2026 camrip.1080p", 10, [2040]),
    release("Resident Evil Nintendo Switch NSP", 11, [1090], "rutracker"),
]


async def find(query, category, results):
    Cog.raw = results
    ranked, _e, _q, _f = await Cog()._find_torrents(query, category)
    return sorted(r["title"] for r in ranked), getattr(ranked, "hidden", None), getattr(ranked, "other", None)


async def filtering():
    print("\n== which releases each search shows ==")
    for category in ("Movies", "Series", "PC", "Music"):
        shown, hidden, other = await find("Resident Evil", category, MIXED())
        print(f"  {category:6}: {len(shown)} shown | other categories: {other} | cam: {hidden}")
        print("         ", [t[:34] for t in shown])
    shown, hidden, other = await find("Resident Evil", "Anime", MIXED())
    print("  a category without a rule:", len(shown), "shown | other:", other)

    print("\n== the setting ==")
    prefs = Cog()._load_preferences()
    print("  Movies hide_other_kinds:", prefs["categories"]["Movies"].get("hide_other_kinds"))
    real = SM.TorrentSearchMixin._load_preferences
    off = {**prefs, "categories": {**prefs["categories"], "Movies": {**prefs["categories"]["Movies"], "hide_other_kinds": False}}}
    SM.TorrentSearchMixin._load_preferences = lambda self: off
    shown, hidden, other = await find("Resident Evil", "Movies", MIXED())
    print("  turned off:", len(shown), "shown | other:", other, "| cam:", hidden)
    gone = {**prefs, "categories": {**prefs["categories"], "Movies": {k: v for k, v in prefs["categories"]["Movies"].items() if k != "hide_other_kinds"}}}
    SM.TorrentSearchMixin._load_preferences = lambda self: gone
    shown, hidden, other = await find("Resident Evil", "Movies", MIXED())
    print("  a preferences file without the key:", len(shown), "shown | other:", other)
    SM.TorrentSearchMixin._load_preferences = real


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


async def messages():
    print("\n== what the person sees ==")
    Cog.raw = MIXED()
    i = Inter(); await Cog()._search(i, query="Resident Evil", category="Movies")
    print("  footer with results:", last(i)["embed"].footer.text)

    Cog.raw = [release("Resident Evil Requiem (2026) FULL UNLOCKED", 5, [4050]), release("Resident Evil Cosplay Clip", 7, [6000]),
               release("Resident Evil 2026 camrip.1080p", 10, [2040])]
    i = Inter(); await Cog()._search(i, query="Resident Evil 2026", category="Movies")
    k = last(i)
    print("  nothing left:", k["content"].replace("\n", " | "))
    print("  buttons:", [b.label for b in k["view"].children])
    press = Inter(); await k["view"].children[0].callback(press)
    print("  pressed:", last(press)["content"][:46])
    for e in W.all_entries(): W.remove(e["id"])

    Cog.raw = [release("Resident Evil Cosplay Clip", 7, [6000]), release("Resident Evil Requiem", 5, [4050])]
    i = Inter(); await Cog()._search(i, query="Resident Evil", category="Movies")
    print("  only other categories:", last(i)["content"].splitlines()[0])


async def watchlist():
    print("\n== the watchlist uses the same filter ==")
    mon = WM.WatchlistMonitor(types.SimpleNamespace(bot=None))
    entry = {"query": "Resident Evil", "category": "Movies"}
    Cog.raw = [release("Resident Evil Requiem (2026) FULL UNLOCKED", 5, [4050]), release("Resident Evil Cosplay Clip", 7, [6000])]
    print("  only other categories:", await mon.look_for(Cog(), entry))
    Cog.raw.append(release("Resident Evil 2026 1080p WEB-DL", 1, [2040]))
    print("  a movie turns up     :", await mon.look_for(Cog(), entry))
    print("  the same, as PC      :", await mon.look_for(Cog(), {"query": "Resident Evil", "category": "PC"}))


async def main():
    await filtering()
    await messages()
    await watchlist()

asyncio.run(main())
