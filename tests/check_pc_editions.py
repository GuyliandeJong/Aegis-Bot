"""PC searches without the edition words: the search is also run without "Digital Deluxe Edition" and the like, the two lists are merged without duplicates, relevance is judged on the game's name with a small bonus for a release that names the edition, and the search does not change for other categories, queries without edition words, a failing second search or a failing first one."""
import asyncio
import logging
import os
import sys
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith(("JELLYFIN_", "PC_GAMES"))]:
    del os.environ[key]
os.environ.update({"JACKETT_URL": "http://127.0.0.1:1", "JACKETT_API_KEY": "k"})

import modules.torrents.torrent.search as SM
from modules.torrents.torrent.formatting import TorrentFormattingMixin
import isolate  # state files go to a temporary folder
SM.TorrentSearchMixin.PREFERENCES_FILE = Path(__file__).resolve().parent.parent / "config" / "torrent_preferences.json"


def release(title, number, seeders, indexer="rutracker", size=140 * 1024 ** 3, info_hash=True):
    return {"title": title, "magnet": f"magnet:?xt=urn:btih:{number:040x}", "indexer": indexer, "seeders": seeders, "leechers": 1, "grabs": 0,
            "size": size, "info_hash": f"{number:040x}" if info_hash else "", "uploader": "", "categories": [4000]}


EXACT = "Phantom Blade Zero - Digital Deluxe Edition"
CORE = "Phantom Blade Zero"
DELUXE_A = release("Phantom Blade Zero Digital Deluxe Edition ALL DLC [Hypervisor]", 1, 31, "1337x")
DELUXE_B = release("Phantom Blade Zero - Digital Deluxe Edition (2026) (v.302256-17280) + 2 DLC [RUS + ENG + 13 / ENG + 1]", 2, 14)
DELUXE_C = release("Phantom Blade Zero - Digital Deluxe Edition (2026) (v.302256-17280) + 2 DLC [RUS + ENG + 13 / ENG + 1]", 3, 9)
PLAIN = release("[DL] Phantom Blade Zero [P] [RUS + ENG + 13 /RUS + ENG + 1] (2026, Action) (302256-1728)0 + 2 DLC [Portable]", 4, 20)
DEMO = release("Phantom Blade Zero Demo", 5, 3, "1337x", 4 * 1024 ** 3)


class Cog(TorrentFormattingMixin, SM.TorrentSearchMixin):
    answers = {}
    asked = []

    async def _check_jellyfin(self, query, category): return []

    async def _run_search(self, service, query):
        Cog.asked.append(query)
        answer = Cog.answers.get(query, [])
        if isinstance(answer, Exception):
            raise answer
        return ([dict(r) for r in answer], {})


async def find(query, category, answers):
    Cog.answers = answers; Cog.asked = []
    ranked, errors, searched, fallback = await Cog()._find_torrents(query, category)
    return ranked


def short(title): return title.replace("Phantom Blade Zero", "PBZ")[:46]


async def main():
    print("== the search without the edition words ==")
    for query in (EXACT, "Baldur's Gate 3 - Game of the Year Edition", "Fallout 4 GOTY", "Hades II Deluxe Edition", "Doom Eternal Ultimate Edition",
                  "Hades II", "Deluxe Edition", "The Complete Edition", "Cyberpunk 2077: Definitive Edition"):
        print(f"  {query!r:50} -> {SM.TorrentSearchMixin._core_query(query, 'PC')!r}")
    print("  the same query as Movies ->", SM.TorrentSearchMixin._core_query(EXACT, "Movies"), "| as Music ->", SM.TorrentSearchMixin._core_query(EXACT, "Music"))

    print("\n== the release that leaves the edition out ==")
    ranked = await find(EXACT, "PC", {EXACT: [DELUXE_A, DELUXE_B, DELUXE_C], CORE: [DELUXE_B, DELUXE_C, PLAIN, DEMO]})
    print("  searched:", Cog.asked)
    print("  results:", len(ranked), "| each once:", len({r["info_hash"] for r in ranked}) == len(ranked))
    for r in ranked:
        print(f"   {r['score']:4} {short(r['title']):48} {r['score_reason']}")
    print("  the plain release is offered:", any(r["info_hash"] == PLAIN["info_hash"] for r in ranked))
    print("  also searched:", ranked.also_searched)
    embed = Cog()._build_search_embed(EXACT, "PC", ranked, {})
    print("  footer:", embed.footer.text.split(" · ")[-1])

    print("\n== what the edition words add ==")
    only_plain = await find(EXACT, "PC", {EXACT: [], CORE: [PLAIN]})
    print("  nothing for the exact search, one for the other ->", [short(r["title"]) for r in only_plain], "| searched:", Cog.asked)
    same = {"deluxe": release("Phantom Blade Zero Digital Deluxe Edition", 11, 50), "standard": release("Phantom Blade Zero Standard", 12, 50)}
    ranked = await find(EXACT, "PC", {EXACT: [same["deluxe"]], CORE: [same["standard"]]})
    scores = {short(r["title"]): (r["score"], r["score_reason"].split(",")[0]) for r in ranked}
    print("  equal seeders: edition named ahead by", scores["PBZ Digital Deluxe Edition"][0] - scores["PBZ Standard"][0], "|", scores)

    print("\n== a duplicate without an info hash ==")
    a = release("Phantom Blade Zero Repack", 21, 5, "rutracker", info_hash=False)
    b = release("Phantom Blade Zero Repack", 22, 5, "rutracker", info_hash=False)
    c = release("Phantom Blade Zero Repack", 23, 5, "1337x", info_hash=False)
    ranked = await find(EXACT, "PC", {EXACT: [a], CORE: [b, c]})
    print("  same indexer and title once, another indexer kept:", sorted(r["indexer"] for r in ranked))

    print("\n== unchanged searches ==")
    for query, category in (("Hades II", "PC"), (EXACT, "Movies"), ("Deluxe Edition", "PC"), (EXACT, "Music"), (EXACT, "Series")):
        await find(query, category, {query: [DELUXE_A]})
        print(f"  {query!r:48} {category:7} -> searched: {Cog.asked}")
    ranked = await find("Hades II", "PC", {"Hades II": [release("Hades II", 31, 10)]})
    print("  no second search -> also searched:", ranked.also_searched, "| footer has it:", "also searched" in Cog()._build_search_embed("Hades II", "PC", ranked, {}).footer.text)

    print("\n== when a search fails ==")
    ranked = await find(EXACT, "PC", {EXACT: [DELUXE_A], CORE: RuntimeError("Jackett timed out")})
    print("  the other search fails -> results:", len(ranked), "| also searched:", ranked.also_searched)
    try:
        await find(EXACT, "PC", {EXACT: RuntimeError("Jackett down"), CORE: [PLAIN]})
        print("  the exact search fails -> no error")
    except RuntimeError as ex:
        print("  the exact search fails -> raises:", ex)
    ranked = await find(EXACT, "PC", {EXACT: [], CORE: []})
    print("  both find nothing -> results:", len(ranked), "| searched:", Cog.asked[:2], "plus", len(Cog.asked) - 2, "fallback queries")

asyncio.run(main())
