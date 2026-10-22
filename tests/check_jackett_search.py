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

import json
import tempfile
from pathlib import Path

c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
import discord
import modules.torrents.torrent.search as SM
from modules.torrents.torrent.formatting import TorrentFormattingMixin
from modules.torrents.jackett_service import JackettService
PREFS = Path(__file__).resolve().parent.parent / "config" / "torrent_preferences.json"
SM.TorrentSearchMixin.PREFERENCES_FILE = PREFS


class Cog(TorrentFormattingMixin, SM.TorrentSearchMixin):
    pass

QUERIES = []
NS = 'xmlns:torznab="http://torznab.com/schemas/2015/feed"'


def item(title, number, uploader=""):
    # Jackett puts the uploader in the description, HTML-escaped in XML.
    description = f"<description>Uploader: {uploader}&lt;br&gt;{title}</description>" if uploader else ""
    return (f"<item><title>{title}</title>{description}<size>5000000000</size>"
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
        items = (item("Castlevania: Belmont’s Curse [P] [RUS + ENG + 9] (2026, Action) [Portable]", 1045, "InsaneRamZes")
                 + item("[Nintendo Switch] Castlevania Belmont’s Curse [NSP][RUS/Multi11]", 1422, "omg_gods")
                 + item("Castlevania: Belmont’s Curse [Repack] (2026)", 900, "faker99"))
    elif indexer == "thepiratebay":
        # Double-escaped in the feed, so the title still holds entities.
        items = item("Castlevania: Belmont&amp;rsquo;s Curse &amp;ndash; Midnight Edition", 6)
    elif indexer == "1337x":
        items = item("Castlevania Belmont s Curse Midnight Edition FitGirl Repack", 2297, "FitGirl")
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

        print("\n== uploaders ==")
        print("  parsed:", sorted((r["indexer"], r["uploader"]) for r in results))
        print("  plain description:", repr(JackettService.uploader("Uploader: DODI")), "| none:", repr(JackettService.uploader("<b>no uploader</b>")))
        print("  trusted in the shipped preferences:", json.loads(PREFS.read_text())["trusted_uploaders"])
        cog = Cog()
        ranked, _, _, _ = await cog._find_torrents("Castlevania: Belmont's Curse", "PC")
        for r in ranked:
            print(f"  {r['score']:4} {r['seeders']:5} {r['uploader']:13} trusted={r['trusted']!s:5} {r['score_reason']}")
        embed = cog._build_search_embed("Castlevania: Belmont's Curse", "PC", ranked, {})
        print("  field:", embed.fields[0].value.split("\n")[0], "|", [f.value.split("\n")[0] for f in embed.fields if "by " in f.value][:1])
        view = types.SimpleNamespace(results=ranked, torrent_cog=cog)
        options = SM.TorrentSearchSelect(view).options
        print("  dropdown:", options[0].description, "|", options[-1].description)

        ranked, _, _, _ = await cog._find_torrents("Castlevania: Belmont's Curse", "Movies")
        print("  Movies category: trusted=", any(r["trusted"] for r in ranked), "| names still shown:", sorted({r["uploader"] for r in ranked if r["uploader"]}))

        print("\n== blocked uploader ==")
        custom = json.loads(PREFS.read_text()); custom["blocked_uploaders"] = ["faker99"]
        temporary = Path(tempfile.mkdtemp()) / "prefs.json"; temporary.write_text(json.dumps(custom))
        SM.TorrentSearchMixin.PREFERENCES_FILE = temporary
        ranked, _, _, _ = await Cog()._find_torrents("Castlevania: Belmont's Curse", "PC")
        faker = next(r for r in ranked if r["uploader"] == "faker99")
        print("  faker99:", faker["score"], faker["score_reason"], "| last:", ranked[-1]["uploader"] == "faker99")
        SM.TorrentSearchMixin.PREFERENCES_FILE = PREFS

        print("\n== nothing to simplify ==")
        QUERIES.clear()
        await service.search("Dune")
        print("  queries:", QUERIES)
    finally:
        await service.close()
        await runner.cleanup()

asyncio.run(main())
