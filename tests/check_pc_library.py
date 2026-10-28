"""/torrent search for the PC category looks in PC_GAMES_DIR first: matching of game names (numbers kept, release names, apostrophes), a folder that is off, missing or slow, the embed with its button, and the button going on to Jackett without looking again."""
import asyncio
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
    del os.environ[key]
os.environ.pop("PC_GAMES_DIR", None)

import discord
from modules.torrents import pc_library as PL
import modules.torrents.torrent.search as SM
import isolate  # state files go to a temporary folder

GAMES = Path(tempfile.mkdtemp(prefix="aegis-games-"))
NAMES = [
    "Cyberpunk.2077.v2.1-GOG", "Football Manager 2024", "Hades II", "Castlevania Belmonts Curse [DODI Repack]",
    "Baldur's Gate 3", "The Witcher 3 Wild Hunt", "Portal 2.iso", "Stardew Valley (2016)", "Hades",
]
for name in NAMES:
    (GAMES / name).mkdir()
(GAMES / "readme.txt").write_text("not a game")


class Message:
    def __init__(s): s.id = 1; s.flags = types.SimpleNamespace(ephemeral=False); s.channel = types.SimpleNamespace(id=10)


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
        s.log = []; s.message = Message(); s.response = Response(s); s.channel = s.message.channel
        s.user = types.SimpleNamespace(id=uid, mention=f"<@{uid}>")
    async def edit_original_response(s, **k):
        s.log.append(("edit_original", k))
        if isinstance(k.get("view"), discord.ui.View): k["view"].to_components()
        return s.message
    async def original_response(s): return s.message


SEARCHED = []


class Cog(SM.TorrentSearchMixin):
    async def _check_jellyfin(self, query, category): return []
    async def _find_torrents(self, query, category):
        SEARCHED.append((query, category))
        return ([], {}, query, False)


def listed(query):
    return asyncio.run(PL.find(query))


print("== matching ==")
os.environ["PC_GAMES_DIR"] = str(GAMES)
for query in ("cyberpunk 2077", "Football Manager 2024", "Football Manager 2025", "Hades", "Hades II", "Belmonts Curse",
              "Belmont's Curse", "Baldurs Gate 3", "Baldur's Gate 3", "The Witcher 3", "Witcher", "Portal 2", "Portal", "Stardew Valley", "Elden Ring"):
    print(f"  {query:24} -> {listed(query)}")
print("  the readme is a file but not a game:", listed("readme"))

print("\n== off, missing, unreadable ==")
os.environ["PC_GAMES_DIR"] = ""
calls = []
real = os.scandir
os.scandir = lambda *a, **k: calls.append(a) or real(*a, **k)
print("  not configured:", listed("hades"), "| folder listings:", len(calls))
os.environ["PC_GAMES_DIR"] = str(GAMES / "does-not-exist")
print("  missing folder:", listed("hades"))
os.environ["PC_GAMES_DIR"] = str(GAMES / "readme.txt")
print("  a file, not a folder:", listed("hades"))
os.scandir = real

print("\n== a slow share ==")
os.environ["PC_GAMES_DIR"] = str(GAMES)
import time
PL._list_names = lambda folder: time.sleep(1)
PL.LIST_TIMEOUT = 0.1
print("  slow folder gives no hits:", listed("hades"))


async def search():
    print("\n== /torrent search, category PC ==")
    import importlib
    importlib.reload(PL)
    SM.pc_library = PL
    os.environ["PC_GAMES_DIR"] = str(GAMES)

    i = Inter()
    await Cog()._search(i, query="Cyberpunk 2077", category="PC")
    k = i.log[-1][1]
    print("  title:", k["embed"].title)
    print("  lines:", [l for l in k["embed"].description.splitlines() if l.startswith("•")])
    print("  shows the folder path:", str(GAMES) in k["embed"].description, "| buttons:", [b.label for b in k["view"].children])
    print("  Jackett searched yet:", SEARCHED)

    other = Inter(uid=2)
    print("  someone else may press:", await k["view"].interaction_check(other))
    press = Inter(uid=1)
    await k["view"].children[0].callback(press)
    print("  owner presses -> Jackett searched:", SEARCHED, "| embed shown again:", any(
        e[1].get("embed") is not None and e[1]["embed"].title == "Already in your PC games folder" for e in press.log))

    SEARCHED.clear()
    i = Inter()
    await Cog()._search(i, query="Elden Ring", category="PC")
    print("  no hit -> straight to Jackett:", SEARCHED)

    SEARCHED.clear()
    i = Inter()
    await Cog()._search(i, query="Cyberpunk 2077", category="Movies")
    print("  other category ignores the folder:", SEARCHED)

    SEARCHED.clear()
    os.environ["PC_GAMES_DIR"] = ""
    i = Inter()
    await Cog()._search(i, query="Cyberpunk 2077", category="PC")
    print("  setting empty:", SEARCHED)

    os.environ["PC_GAMES_DIR"] = str(GAMES)
    for n in range(14):
        (GAMES / f"Long Game Name {n:02}").mkdir()
    i = Inter()
    await Cog()._search(i, query="Long Game Name", category="PC")
    print("  many hits:", [l for l in i.log[-1][1]["embed"].description.splitlines() if l.startswith("…")])


asyncio.run(search())
