"""Music: the audio format is read from the title and the category (FLAC spellings, other lossless codecs, lossy), FLAC ranks first in a normal Music search, a FLAC-only search offers FLAC and nothing else, the option is ignored for other categories, and a watchlist entry keeps looking for FLAC through the button, the check and the Found message."""
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
import modules.torrents.torrent.watchlist as WV
import modules.torrents.watchlist_monitor as WM
from modules.torrents.torrent.formatting import TorrentFormattingMixin
import isolate  # state files go to a temporary folder
W.STATE_FILE = isolate.DATA / "watchlist.json"
WM.WatchlistMonitor.STATE_FILE = isolate.DATA / "watchlist_monitor.json"
SM.TorrentSearchMixin.PREFERENCES_FILE = Path(__file__).resolve().parent.parent / "config" / "torrent_preferences.json"


def release(title, number, categories=None, seeders=50):
    result = {"title": title, "magnet": f"magnet:?xt=urn:btih:{number:040x}", "indexer": "rutracker", "seeders": seeders,
              "leechers": 1, "grabs": 0, "size": 400_000_000, "info_hash": f"{number:040x}", "uploader": ""}
    if categories is not None:
        result["categories"] = categories
    return result


A = "Aurora Falls - Night Drive"
print("== the format of a release ==")
CASES = [
    (f"{A} (2024) [FLAC]", [3040], "flac"),
    (f"{A} (2024) FLAC24", None, "flac"),
    (f"{A} 24bit-FLAC", None, "flac"),
    (f"{A} [FLAC 24-96]", None, "flac"),
    (f"{A} FLAC + MP3", [3040], "flac"),
    ("Aurora Flacco - Night Drive", None, "unknown"),
    (f"{A} MP3 320kbps", [3010], "lossy"),
    (f"{A} 256 kbps", None, "lossy"),
    (f"{A} MP3", [3040], "lossy"),
    (f"{A}", [3010], "lossy"),
    (f"{A} (Lossless)", None, "lossless"),
    (f"{A}", [3040], "lossless"),
    (f"{A} APE", [3040], "other lossless"),
    (f"{A} [ALAC]", None, "other lossless"),
    (f"{A} WavPack", None, "other lossless"),
    (f"{A} 2024", None, "unknown"),
]
for title, categories, _expected in CASES:
    r = release(title, 1, categories)
    print(f"  {title:34} {str(categories):8} -> {SM.TorrentSearchMixin._audio_format(r):15} FLAC search: {SM.TorrentSearchMixin._is_flac(r)}")


class Cog(TorrentFormattingMixin, SM.TorrentSearchMixin, WV.TorrentWatchlistMixin):
    raw = []

    async def _check_jellyfin(self, query, category): return []
    async def _run_search(self, service, query): return ([dict(r) for r in Cog.raw], {})


MIXED = lambda: [
    release(f"{A} 2024 MP3 320kbps", 1, [3010], 90),
    release(f"{A} (2024) [FLAC]", 2, [3040], 80),
    release(f"{A} (2024) FLAC24 24bit", 3, [3040], 70),
    release(f"{A} (2024) (Lossless)", 4, [3040], 60),
    release(f"{A} 2024", 5, None, 55),
    release(f"{A} 2024 APE", 6, [3040], 50),
    release(f"{A} 2024 FLAC + MP3", 7, [3040], 40),
]


async def find(category, lossless=False, results=None):
    Cog.raw = results if results is not None else MIXED()
    kwargs = {"lossless": True} if lossless else {}
    ranked, _e, _q, _f = await Cog()._find_torrents("Aurora Falls Night Drive", category, **kwargs)
    return ranked


def short(title): return title.replace(f"{A} ", "")


async def ranking():
    print("\n== a normal Music search ranks by format ==")
    ranked = await find("Music")
    for r in ranked:
        print(f"  {r['score']:4} {short(r['title']):22} {r['score_reason']}")
    print("  hidden:", ranked.hidden, ranked.other, ranked.not_flac)

    print("\n== a FLAC-only search ==")
    ranked = await find("Music", lossless=True)
    print("  shown :", [short(r["title"]) for r in ranked])
    print("  hidden:", ranked.not_flac, "not FLAC | cam:", ranked.hidden, "| other categories:", ranked.other)
    print("  footer:", Cog()._build_search_embed("q", "Music", ranked, {}).footer.text)
    ranked = await find("Movies", lossless=True)
    print("  asked for Movies, nothing hidden for format:", ranked.not_flac)


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


def clear():
    for e in W.all_entries(): W.remove(e["id"])


async def flows():
    print("\n== the option ==")
    calls = []

    class Spy(Cog):
        async def _find_torrents(self, query, category, **kwargs):
            calls.append((category, kwargs))
            return await super()._find_torrents(query, category, **kwargs)

    Cog.raw = MIXED()
    for category, lossless in (("Music", True), ("Music", False), ("Movies", True)):
        await Spy()._search(Inter(), query="Aurora Falls Night Drive", category=category, lossless=lossless)
    print("  find called with:", calls)

    print("\n== nothing FLAC found ==")
    Cog.raw = [release(f"{A} 2024 MP3 320kbps", 1, [3010]), release(f"{A} 2024 APE", 2, [3040]), release(f"{A} 2024", 3, None)]
    i = Inter(); await Cog()._search(i, query="Aurora Falls Night Drive", category="Music", lossless=True)
    k = last(i)
    print("  message:", k["content"].replace("\n", " | "))
    press = Inter(); await k["view"].children[0].callback(press)
    print("  pressed:", last(press)["content"][:48])
    print("  entry:", [(e["query"], e["category"], e.get("lossless")) for e in W.all_entries()])
    print("  the same title as a normal search is another entry:", W.add("Aurora Falls Night Drive", "Music", 1, 55)[0], "| a second FLAC one is refused:", W.add("Aurora Falls Night Drive", "Music", 1, 55, lossless=True)[0])
    print("  /torrent watchlist:", [l.split(", since")[0] for l in WV.watchlist_embed(W.all_entries()).description.splitlines()[:2]])
    clear()

    print("\n== watching from a list of FLAC results ==")
    Cog.raw = MIXED()
    i = Inter(); await Cog()._search(i, query="Aurora Falls Night Drive", category="Music", lossless=True)
    k = last(i)
    print("  buttons:", [getattr(b, "label", None) or "dropdown" for b in k["view"].children], "| footer:", k["embed"].footer.text.split(" · ")[-1])
    button = next(b for b in k["view"].children if getattr(b, "label", "") == "Watch for a better release")
    press = Inter(); await button.callback(press)
    entry = W.all_entries()[-1]
    print("  entry:", entry["query"], entry["category"], "| FLAC:", entry.get("lossless"), "| seen:", len(entry["seen"]))
    i = Inter(); await Cog()._search(i, query="Aurora Falls Night Drive", category="Music")
    button = next(b for b in last(i)["view"].children if getattr(b, "label", "") == "Watch for a better release")
    press = Inter(); await button.callback(press)
    print("  from a normal search:", [e.get("lossless") for e in W.all_entries()])
    clear()


async def watchlist():
    print("\n== the watchlist keeps looking for FLAC ==")
    asked = []

    class Spy(Cog):
        async def _find_torrents(self, query, category, **kwargs):
            asked.append(kwargs)
            return ([release(f"{A} FLAC", 9, [3040])], {}, query, False)

    mon = WM.WatchlistMonitor(types.SimpleNamespace(bot=None))
    flac_entry = {"id": "a", "query": "Aurora Falls Night Drive", "category": "Music", "lossless": True}
    old_entry = {"id": "b", "query": "Aurora Falls Night Drive", "category": "Music"}
    print("  FLAC entry :", await mon.look_for(Spy(), flac_entry), "| asked with", asked[-1])
    print("  old entry  :", await mon.look_for(Spy(), old_entry), "| asked with", asked[-1])

    searches = []

    class Torrent:
        async def _search(self, interaction, query, category, **kwargs): searches.append((query, category, kwargs))

    bot = types.SimpleNamespace(get_cog=lambda name: Torrent())
    for entry in (flac_entry, old_entry):
        view = WV.WatchlistFoundView(bot, {**entry, "user_id": "1"})
        await view._show(Inter())
    print("  Show results:", searches)

    # A FLAC-only search that finds only MP3 is not "found" by the check.
    Cog.raw = [release(f"{A} MP3 320kbps", 1, [3010])]
    print("  only MP3, FLAC entry :", await mon.look_for(Cog(), flac_entry))
    print("  only MP3, normal entry:", await mon.look_for(Cog(), old_entry))


async def main():
    await ranking()
    await flows()
    await watchlist()

asyncio.run(main())
