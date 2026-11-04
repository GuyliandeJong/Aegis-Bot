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


def release(title, number, categories=None, seeders=50, indexer="rutracker"):
    result = {"title": title, "magnet": f"magnet:?xt=urn:btih:{number:040x}", "indexer": indexer, "seeders": seeders,
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
    (f"{A} Metal (2026) 320_kbps Beats", [3000]),
    (f"{A} [24-96]", [3000]),
    (f"{A} 24bit 96kHz", None),
    (f"{A} 24/48", None),
    (f"{A} Hi-Res", None),
    (f"{A} 24/48 MP3", None),
    (f"{A} Night 24 Hours", None),
    (f"{A} 2024", None),
]
for title, categories in CASES:
    r = release(title, 1, categories)
    print(f"  {title:34} {str(categories):8} -> {SM.TorrentSearchMixin._audio_format(r):15} offered: {SM.TorrentSearchMixin._is_lossless(r)}")


print("\n== RuTracker's Hi-Res forums ==")
HIRES = "[TR24][OF][LDR] Aurora Falls - Night Drive - 2026 (Alternative Metal)"
for label, title, categories, indexer in [
    ("hi-res forum, no format in the title", HIRES, [3000, 101890], "rutracker"),
    ("another hi-res forum (Rock)", "Artist - Album - 2020 (Rock)", [3000, 101755], "rutracker"),
    ("the same numbers on another indexer", HIRES, [3000, 101890], "thepiratebay"),
    ("a RuTracker forum that is not Hi-Res", "Artist - Album - 2020 (Rock)", [3000, 101757 + 1], "rutracker"),
    ("hi-res forum, but MP3 in the title", HIRES + " MP3", [3000, 101890], "rutracker"),
    ("plain Audio, no forum", HIRES, [3000], "rutracker"),
]:
    r = release(title, 1, categories, indexer=indexer)
    print(f"  {label:38} -> {SM.TorrentSearchMixin._audio_format(r):9} offered: {SM.TorrentSearchMixin._is_lossless(r)}")


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


async def quality():
    print("\n== bit depth and discographies ==")
    for title, categories in [
        (f"{A} [FLAC 24-96]", None), (f"{A} FLAC 24bit", None), (f"{A} Hi-Res", None),
        (f"{A} FLAC 16-bit", None), (f"{A} FLAC 16/44.1", None), (f"{A} [FLAC] 16-44", None),
        (f"{A} FLAC", None), (f"{A} Night 24 Hours FLAC", None), (f"{A} FLAC 1624", None),
        (HIRES, [3000, 101890]),
    ]:
        r = release(title, 1, categories)
        label = title[len(A):][:28] if title.startswith(A) else "RuTracker Hi-Res forum"
        print(f"  depth {label:30} -> {SM.TorrentSearchMixin._bit_depth(r)}")
    for title, query in [
        (f"{A} - Discography (1998-2024) FLAC", A), (f"{A} Дискография FLAC", A), (f"{A} 1983-2016 FLAC", A),
        (f"{A} (2015-2016) FLAC", A), (f"{A} Anthology FLAC", A), (f"{A} Box Set FLAC", A),
        (f"{A} Complete Collection FLAC", A), (f"{A} Discography FLAC", f"{A} discography"),
    ]:
        print(f"  discography {title[len(A):][:34]:36} query {query[len(A):] or 'plain':14} -> {SM.TorrentSearchMixin._is_discography(title, query)}")

    print("\n== how they rank ==")
    ranked = await find("Music", [release(f"{A} (2024) [FLAC] 16-bit", 1, None, 50), release(f"{A} (2024) [FLAC] 24-bit", 2, None, 50)])
    print("  same seeders      :", [(short(r["title"]), r["score"]) for r in ranked])
    ranked = await find("Music", [release(f"{A} (2024) [FLAC] 16-bit", 1, None, 50), release(f"{A} (2024) [ALAC] 24-bit", 2, None, 50)])
    print("  24-bit ALAC vs 16-bit FLAC:", [short(r["title"]) for r in ranked])
    pack = release(f"{A} - Discography (1998-2024) [FLAC]", 3, None, 900)
    album = release(f"{A} (2024) [FLAC]", 4, None, 5)
    ranked = await find("Music", [dict(pack), dict(album)])
    print("  pack with 900 seeders vs album with 5:", [(short(r["title"]), r["score_reason"].split(",")[0]) for r in ranked])
    Cog.raw = [dict(pack), dict(album)]
    asked, _e, _q, _f = await Cog()._find_torrents(f"{A} discography", "Music")
    print("  the search asks for a discography:", [short(r["title"]) for r in asked])
    ranked = await find("Movies", [release("Some Film 1998-2024 Collection 1080p", 5, [2040], 5)])
    print("  other categories:", [r["score_reason"] for r in ranked])

    real = with_preferences(lambda p: {**p, "categories": {**p["categories"], "Music": {k: v for k, v in p["categories"]["Music"].items() if k not in {"bit_depth_scores", "discography_score"}}}})
    ranked = await find("Music", [dict(pack), dict(album)])
    print("  a copy in data/ without the two keys:", [short(r["title"]) for r in ranked])
    SM.TorrentSearchMixin._load_preferences = real
    real = with_preferences(lambda p: {**p, "categories": {**p["categories"], "Music": {**p["categories"]["Music"], "bit_depth_scores": {"24-bit": 0, "16-bit": 0}, "discography_score": 0}}})
    ranked = await find("Music", [dict(pack), dict(album)])
    print("  both set to 0:", [short(r["title"]) for r in ranked])
    SM.TorrentSearchMixin._load_preferences = real


async def main():
    await searches()
    await quality()
    await flows()
    await watchlist()
    await command()

asyncio.run(main())
