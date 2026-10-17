"""Jackett search against a local fake Jackett: an indexer that finds nothing for a query with punctuation is retried without it, even when other indexers found results; titles with HTML entities are decoded."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18119
os.environ.update({"JACKETT_URL": f"http://127.0.0.1:{PORT}", "JACKETT_API_KEY": "k"})
os.environ.pop("JACKETT_INDEXERS", None)  # the default list is checked

from services.jackett_service import JackettService

QUERIES = []
NS = 'xmlns:torznab="http://torznab.com/schemas/2015/feed"'


def item(title, number):
    return (f"<item><title>{title}</title><size>5000000000</size>"
            f'<link>magnet:?xt=urn:btih:{number:040x}</link>'
            f'<torznab:attr name="seeders" value="{number}"/>'
            f'<torznab:attr name="infohash" value="{number:040x}"/></item>')


async def torznab(request):
    indexer, query = request.match_info["indexer"], request.query.get("q", "")
    QUERIES.append((indexer, query))
    items = ""
    if indexer == "rutracker" and "'" not in query and ":" not in query and "Belmont" in query:
        # rutracker writes the title with a curly apostrophe and only
        # matches words, so the query with punctuation finds nothing.
        items = item("Castlevania: Belmont’s Curse [P] [RUS + ENG + 9] (2026, Action) [Portable]", 1058)
    elif indexer == "thepiratebay":
        # Double-escaped in the feed, so the title still holds entities.
        items = item("Castlevania: Belmont&amp;rsquo;s Curse &amp;ndash; Midnight Edition", 6)
    elif indexer == "1337x":
        items = item("Castlevania Belmont s Curse Midnight Edition FitGirl Repack", 2297)
    body = f'<?xml version="1.0"?><rss {NS}><channel>{items}</channel></rss>'
    return web.Response(text=body, content_type="application/xml")


async def main():
    app = web.Application(); app.add_routes([web.get("/api/v2.0/indexers/{indexer}/results/torznab/api", torznab)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    service = JackettService()
    try:
        print("== simplified query ==")
        for q in ("Castlevania: Belmont's Curse", "Castlevania: Belmont’s Curse", "Spider-Man: No Way Home", "Ходячий замок", "Dune"):
            print(f"  {q!r:32} -> {service.simplified_query(q)!r}")

        print("\n== search ==")
        results, errors = await service.search("Castlevania: Belmont's Curse")
        for r in sorted(results, key=lambda r: r["indexer"]):
            print(f"  {r['indexer']:13} {r['title']}")
        print("  errors:", errors)
        print("  queries:", QUERIES)

        print("\n== nothing to simplify ==")
        QUERIES.clear()
        await service.search("Dune")
        print("  queries:", QUERIES)
    finally:
        await service.close()
        await runner.cleanup()

asyncio.run(main())
