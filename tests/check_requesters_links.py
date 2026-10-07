"""Share-link search and per-requester notifications, real discord.py + aiohttp, local fakes only."""
import asyncio
import json
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(DISCORD_USER_ID="111", QBITTORRENT_HOST="h", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", ALERT_CHANNEL_ID=1, MONITOR_INTERVAL=60)
sys.modules["config.settings"] = c
for k in list(os.environ):
    if k.startswith("JELLYFIN_"): del os.environ[k]

import discord
import commands.torrent.search as SM
import commands.torrent.add as AM
import services.torrent_requesters as R
import services.share_link_service as SL
from commands.torrent.formatting import TorrentFormattingMixin
from commands.torrent.constants import ALLOWED_CATEGORIES
import isolate  # state files go to a temporary folder

R.STATE_FILE = Path(tempfile.mkdtemp()) / "requesters.json"
PORT = 18100
BASE = f"http://127.0.0.1:{PORT}"
WIKIDATA = {"/m/0bscw": [("Gattaca", "Q11424", "1997-10-24T00:00:00Z", None), ("Gattaca", "Q11424", "1998-03-20T00:00:00Z", None)],
            "/m/0524b41": [("Game of Thrones", "Q5398426", None, "2011-04-17T00:00:00Z")]}
LINKS = {"film": "q=Gattaca&kgmid=/m/0bscw", "series": "q=Game+of+Thrones&kgmid=/m/0524b41", "nokg": "q=Some+Obscure+Title",
         "throttled": "q=Gattaca&kgmid=/m/0000"}
SEEN = []


async def share(request):
    SEEN.append("share"); raise web.HTTPFound(f"{BASE}/hop?q={request.match_info['id']}")


async def hop(request):
    key = request.query["q"]
    if key not in LINKS: return web.Response(status=404)
    raise web.HTTPMovedPermanently(f"{BASE}/search?hl=en&{LINKS[key]}")


async def search_page(request):
    SEEN.append("SEARCH PAGE REQUESTED"); return web.Response(text="should never be fetched")


async def sparql(request):
    SEEN.append("wikidata")
    q = request.query["query"]
    kg = q.split('"')[1]
    if kg == "/m/0000": return web.Response(status=429, text="<html>slow down</html>", content_type="text/html")
    rows = []
    for label, kind, date, start in WIKIDATA.get(kg, []):
        row = {"itemLabel": {"value": label}, "type": {"value": f"http://www.wikidata.org/entity/{kind}"}}
        if date: row["date"] = {"value": date}
        if start: row["start"] = {"value": start}
        rows.append(row)
    return web.Response(body=json.dumps({"results": {"bindings": rows}}).encode(), headers={"Content-Type": "application/sparql-results+json"})

# point the service at the local fakes (hosts are normally fixed to Google)
S = SL.ShareLinkService
S.SHARE_HOST = "127.0.0.1"; S.ALLOWED_HOSTS = {"127.0.0.1"}; S.WIKIDATA_SPARQL = f"{BASE}/sparql"
S._is_share_link = classmethod(lambda cls, url: url.startswith(f"{BASE}/s/"))
orig_follow = S._follow


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def defer(s, **k): assert not s.done; s.done = True
    async def send_message(s, text=None, **k): assert not s.done; s.done = True; s.owner.log.append({"content": text})


class Inter:
    def __init__(s, uid=1, name="tester"):
        s.log = []; s.response = Response(s); s.user = types.SimpleNamespace(id=uid, mention=f"<@{uid}>", __str__=lambda: name)
        s.followup = types.SimpleNamespace(send=s._fu)
    async def _fu(s, text=None, **k): s.log.append({"content": text, **k})
    async def edit_original_response(s, **k): s.log.append(k); return types.SimpleNamespace(id=1)
    async def original_response(s): return types.SimpleNamespace(id=1)


SEARCHED = []


class FakeJackett:
    async def search(s, q):
        SEARCHED.append(q)
        return [{"title": f"{q} 1080p x265", "indexer": "1337x", "seeders": 9, "leechers": 1, "grabs": 0, "size": 5,
                 "magnet": "magnet:?xt=urn:btih:abc", "source_url": ""}], {}
    async def close(s): pass


SM.JackettService = FakeJackett


class Cog(TorrentFormattingMixin, AM.TorrentAddMixin, SM.TorrentSearchMixin):
    ALLOWED_CATEGORIES = ALLOWED_CATEGORIES


async def run_search(query, category):
    SEARCHED.clear(); i = Inter()
    await Cog()._search(i, query=query, category=category)
    last = i.log[-1]
    embed = last.get("embed")
    return SEARCHED[:], (embed.description.splitlines()[:2] if embed else last.get("content"))


async def main():
    app = web.Application()
    app.add_routes([web.get("/s/{id}", share), web.get("/hop", hop), web.get("/search", search_page), web.get("/sparql", sparql)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== share links in /torrent search ==")
        for label, query, category in (("film link, Movies", f"{BASE}/s/film", "Movies"), ("series link, but Movies chosen", f"{BASE}/s/series", "Movies"),
                                       ("link without Google id", f"{BASE}/s/nokg", "Movies"), ("Wikidata throttled", f"{BASE}/s/throttled", "Movies"),
                                       ("dead link", f"{BASE}/s/dead", "Movies"), ("plain title (unchanged)", "Gattaca", "Movies")):
            searched, shown = await run_search(query, category)
            print(f"  {label:31} searched Jackett for {searched} -> {shown}")
        print("  Google results page fetched:", "SEARCH PAGE REQUESTED" in SEEN, "(must be False)")
    finally:
        await runner.cleanup()

    print("\n== who gets notified ==")
    class FakeQB:
        HASH = "AAA111"
        async def add_torrent(s, **k): return FakeQB.HASH
        async def get_torrents(s, **k): return [{"hash": FakeQB.HASH.lower(), "name": "T", "category": "Movies", "state": "downloading", "progress": 0}]
        async def close(s): pass
    AM.QBittorrentService = FakeQB
    class V:
        def __init__(s, **k): s.message = None
        def start_refresh(s): pass
    AM.TorrentControlView = V
    cog = Cog(); cog._build_info_embed = lambda *a, **k: discord.Embed(title="x")
    for uid, h in ((222, "AAA111"), (333, "BBB222")):
        FakeQB.HASH = h; await cog._add(Inter(uid=uid), category="Movies", magnet="magnet:?xt=urn:btih:x")
    import monitors.torrent_monitor as TM
    import monitors.torrent_health_monitor as THM
    sent = []
    class Notes:
        def __getattr__(s, level):
            async def send(title, description, **k): sent.append((title, k.get("content")))
            return send
    tm = TM.TorrentMonitor.__new__(TM.TorrentMonitor); tm.notification_service = Notes()
    for h, who in (("aaa111", "partner (222)"), ("bbb222", "user 333"), ("ccc333", "added outside the bot")):
        sent.clear(); await tm.notify_completed({"hash": h, "name": "T", "size": 1, "time_active": 1}, {})
        print(f"  completed, added by {who:22} -> mentions {sent[-1][1]}")
    hm = THM.TorrentHealthMonitor.__new__(THM.TorrentHealthMonitor); hm.notification_service = Notes()
    sent.clear(); await hm.notify_stalled({"hash": "AAA111", "name": "T"}, 700); await hm.notify_error({"hash": "bbb222", "name": "T"})
    await hm.notify_status("t", "d", {"hash": "zzz", "name": "T"})
    print("  stalled / error / other          -> mentions", [s[1] for s in sent])

asyncio.run(main())
