"""Music: the audio format is read from the title and the category (FLAC spellings, other lossless codecs, lossy), a Music search offers any lossless release and nothing else (also without a Music section in the preferences, and unless lossless_only is false), FLAC still ranks first, other categories are untouched, /torrent search has no format option, and a watchlist entry for Music waits for a lossless release."""
import asyncio
import logging
import os
import sys
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(
    ALERT_CHANNEL_ID=10, QBITTORRENT_HOST="h", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5,
    QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", DISCORD_USER_ID="")
sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
    del os.environ[key]
os.environ.update({"JACKETT_URL": "http://127.0.0.1:1", "JACKETT_API_KEY": "k"})

import discord
from discord.ext import commands
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
    (f"{A} (2024) [FLAC]", [3040]),
    (f"{A} (2024) FLAC24", None),
    (f"{A} 24bit-FLAC", None),
    (f"{A} [FLAC 24-96]", None),
    (f"{A} FLAC + MP3", [3040]),
    ("Aurora Flacco - Night Drive", None),
    (f"{A} MP3 320kbps", [3010]),
    (f"{A} 256 kbps", None),
    (f"{A} MP3", [3040]),
    (f"{A}", [3010]),
    (f"{A} (Lossless)", None),
    (f"{A}", [3040]),
    (f"{A} APE", [3040]),
    (f"{A} [ALAC]", None),
    (f"{A} WavPack", None),
    (f"{A} TAK", None),
    (f"{A} Lossless + MP3", None),
    (f"{A} APE + MP3 320kbps", [3010]),
    (f"{A} 2024", None),
]
for title, categories in CASES:
    r = release(title, 1, categories)
    print(f"  {title:34} {str(categories):8} -> {SM.TorrentSearchMixin._audio_format(r):15} offered: {SM.TorrentSearchMixin._is_lossless(r)}")


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
    release(f"{A} 2024 Lossless + MP3", 8, None, 30),
]


async def find(category, results=None):
    Cog.raw = results if results is not None else MIXED()
    ranked, _e, _q, _f = await Cog()._find_torrents("Aurora Falls Night Drive", category)
    return ranked


def short(title): return title.replace(f"{A} ", "")


def with_preferences(change):
    """Run searches with the shipped preferences changed by `change`."""
    real = SM.TorrentSearchMixin._load_preferences
    shipped = real(Cog())
    SM.TorrentSearchMixin._load_preferences = lambda self: change(shipped)
    return real


async def searches():
    print("\n== a Music search offers lossless releases and nothing else ==")
    ranked = await find("Music")
    for r in ranked:
        print(f"  {r['score']:4} {short(r['title']):22} {r['score_reason']}")
    print("  hidden:", ranked.not_lossless, "not lossless | cam:", ranked.hidden, "| other categories:", ranked.other)
    print("  footer:", Cog()._build_search_embed("q", "Music", ranked, {}).footer.text)

    print("\n== without a Music section in the preferences ==")
    real = with_preferences(lambda p: {**p, "categories": {k: v for k, v in p["categories"].items() if k != "Music"}})
    ranked = await find("Music")
    print("  shown:", [short(r["title"]) for r in ranked], "| hidden:", ranked.not_lossless)
    SM.TorrentSearchMixin._load_preferences = real

    print("\n== lossless_only turned off ==")
    real = with_preferences(lambda p: {**p, "categories": {**p["categories"], "Music": {**p["categories"]["Music"], "lossless_only": False}}})
    ranked = await find("Music")
    print("  shown:", len(ranked), "| hidden:", ranked.not_lossless, "| first:", short(ranked[0]["title"]), "| last:", short(ranked[-1]["title"]))
    SM.TorrentSearchMixin._load_preferences = real

    print("\n== other categories are not filtered for format ==")
    plain = lambda: [release(f"{A} 2024 MP3", 1), release(f"{A} 2024", 2), release(f"{A} 2024 FLAC", 3)]
    for category in ("Movies", "Series", "PC"):
        ranked = await find(category, plain())
        print(f"  {category:6}: {len(ranked)} shown | not lossless: {ranked.not_lossless}")


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
    print("\n== nothing lossless found ==")
    Cog.raw = [release(f"{A} 2024 MP3 320kbps", 1, [3010]), release(f"{A} 2024 OGG", 2, None), release(f"{A} 2024", 3, None)]
    i = Inter(); await Cog()._search(i, query="Aurora Falls Night Drive", category="Music")
    k = last(i)
    print("  message:", k["content"].replace("\n", " | "))
    press = Inter(); await k["view"].children[0].callback(press)
    print("  pressed:", last(press)["content"][:48])
    print("  entry:", [(e["query"], e["category"], sorted(set(e) - {"id", "query", "category", "user_id", "channel_id", "added"})) for e in W.all_entries()])
    print("  /torrent watchlist:", WV.watchlist_embed(W.all_entries()).description.splitlines()[0].split(", since")[0])
    clear()

    print("\n== watching from a list of results ==")
    Cog.raw = MIXED()
    i = Inter(); await Cog()._search(i, query="Aurora Falls Night Drive", category="Music")
    k = last(i)
    print("  buttons:", [getattr(b, "label", None) or "dropdown" for b in k["view"].children], "| footer:", k["embed"].footer.text.split(" · ")[-1])
    button = next(b for b in k["view"].children if getattr(b, "label", "") == "Watch for a better release")
    press = Inter(); await button.callback(press)
    entry = W.all_entries()[-1]
    print("  entry:", entry["query"], entry["category"], "| seen:", len(entry["seen"]))


async def watchlist():
    print("\n== the watchlist waits for a lossless release ==")
    mon = WM.WatchlistMonitor(types.SimpleNamespace(bot=None))
    entry = W.all_entries()[-1]
    Cog.raw = [release(f"{A} MP3 320kbps", 1, [3010]), release(f"{A} 2024", 2, None)]
    print("  only MP3 and untagged:", await mon.look_for(Cog(), {"id": "a", "query": "Aurora Falls Night Drive", "category": "Music"}))
    Cog.raw = [release(f"{A} MP3 320kbps", 1, [3010]), release(f"{A} (2024) [FLAC]", 9, [3040])]
    print("  a FLAC release turns up:", await mon.look_for(Cog(), {"id": "a", "query": "Aurora Falls Night Drive", "category": "Music"}))
    print("  an entry from the earlier test version, with lossless: True:", await mon.look_for(Cog(), {"id": "b", "query": "Aurora Falls Night Drive", "category": "Music", "lossless": True}))
    print("  seen releases still ignored:", await mon.look_for(Cog(), {**entry, "seen": [W.result_key(r) for r in Cog.raw]}))
    clear()

    searches_made = []

    class Torrent:
        async def _search(self, interaction, query, category, **kwargs): searches_made.append((query, category, kwargs))

    bot = types.SimpleNamespace(get_cog=lambda name: Torrent())
    view = WV.WatchlistFoundView(bot, {"id": "a", "query": "Aurora Falls Night Drive", "category": "Music", "user_id": "1", "lossless": True})
    await view._show(Inter())
    print("  Show results runs:", searches_made)


async def command():
    print("\n== /torrent search ==")
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    await bot.load_extension("modules.torrents.commands.torrent")
    search = bot.tree.get_command("torrent").get_command("search")
    print("  options:", [o["name"] for o in search.to_dict(bot.tree)["options"]])


async def main():
    await searches()
    await flows()
    await watchlist()
    await command()

asyncio.run(main())
