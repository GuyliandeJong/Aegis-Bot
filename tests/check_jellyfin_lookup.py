"""Jellyfin multi-server lookup and the 'already available' embed."""
import asyncio
import os
import types

import stubs
from stubs import HTTP

os.environ.update({
    "JELLYFIN_1_URL": "http://home:8096", "JELLYFIN_1_API_KEY": "k1", "JELLYFIN_1_NAME": "Home",
    "JELLYFIN_2_URL": "http://friend:8096", "JELLYFIN_2_API_KEY": "k2", "JELLYFIN_2_NAME": "Jellyfin 2",
})
LIB = {
    "http://home:8096": [{"Name": "Dune", "ProductionYear": 2021, "Id": "a", "Type": "Movie"}],
    "http://friend:8096": [{"Name": "Dune", "ProductionYear": 2021, "Id": "b", "Type": "Movie"},
                           {"Name": "Alien: Romulus", "ProductionYear": 2024, "Id": "c", "Type": "Movie"},
                           {"Name": "Arrival", "ProductionYear": 2016, "Id": "d", "Type": "Movie"}],
}
terms = []


def script(method, url, kw):
    base = url.rsplit("/Items", 1)[0]
    term = kw["params"]["SearchTerm"]; terms.append(term)
    if base == "http://friend:8096" and term == "boom": return 500, {}
    return 200, {"Items": [i for i in LIB[base] if term.lower().split()[0] in i["Name"].lower()]}


HTTP.script = script
from services.jellyfin_service import JellyfinService as J
import commands.torrent.search as SM
import isolate  # state files go to a temporary folder

s = J()
print("servers:", [x.name for x in s.servers])


async def find(q): return await J().find_matches(q, "Movies")


for q in ("Dune", "Dune 2021", "Dune 1984", "Arrival", "Alien", "Nothing"):
    m = asyncio.run(find(q)); print(f"  {q!r:12} -> {[x['server'] for x in m]}")
print("  search terms sent for 'Dune 2021':", sorted({t for t in terms if t.startswith('Dune')}))
print("  is_available('Arrival') ->", asyncio.run(J().is_available("Arrival", "Movies"))["server"])
print("  Music category ->", asyncio.run(J().find_matches("Dune", "Music")))

cog = SM.TorrentSearchMixin()
for q in ("Dune", "Arrival"):
    e = cog._build_jellyfin_embed(asyncio.run(find(q)))
    print(f"\n[{e.title}]\n{e.description}")
