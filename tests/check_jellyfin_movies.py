"""/jellyfin movies against two local fake Jellyfin servers: matching movies across servers, watched state per configured user, the filters, search, a server that does not answer, and paging."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18118
for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
    del os.environ[key]
os.environ.update({
    "JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home", "JELLYFIN_1_USER": "Alex",
    "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "f", "JELLYFIN_2_NAME": "Friend", "JELLYFIN_2_USER": "alex",
})

import modules.jellyfin.commands.jellyfin as J
from modules.jellyfin import movie_library as ML
import isolate  # state files go to a temporary folder

# (name, year, tmdb, played)
LIBRARIES = {
    "home": [("Arrival", 2016, "329865", True), ("Dune", 2021, "438631", False), ("Heat", 1995, "", False),
             ("The Thing", 1982, "1091", False), ("Children of Dune", 2003, "", False)],
    "friend": [("Arrival", 2016, "329865", False), ("Dune", 2021, "438631", True), ("Heat", 1995, "", False),
               ("Ronin", 1998, "8195", False), ("Alien", 1979, "348", True), ("Dune: Part Two", 2024, "693134", False)],
}
USERS = {"home": [{"Name": "Alex", "Id": "u-home"}], "friend": [{"Name": "Friend", "Id": "x"}, {"Name": "Alex", "Id": "u-friend"}]}
DOWN = set()
SESSIONS = [
    {"UserName": "alex", "Client": "Jellyfin Android TV", "DeviceName": "Living Room TV",
     "NowPlayingItem": {"Type": "Movie", "Name": "Dune", "ProductionYear": 2021, "RunTimeTicks": 9300 * 10_000_000},
     "PlayState": {"PositionTicks": 5520 * 10_000_000, "IsPaused": False, "PlayMethod": "DirectPlay"}},
    {"UserName": "friend", "Client": "Jellyfin Web", "DeviceName": "Firefox",
     "NowPlayingItem": {"Type": "Episode", "SeriesName": "Fallout", "ParentIndexNumber": 2, "IndexNumber": 3,
                        "Name": "The Beginning", "RunTimeTicks": 3600 * 10_000_000},
     "PlayState": {"PositionTicks": 600 * 10_000_000, "IsPaused": True, "PlayMethod": "Transcode"},
     "TranscodingInfo": {"Height": 720, "Bitrate": 4000000, "TranscodeReasons": ["AudioCodecNotSupported", "SubtitleCodecNotSupported"]}},
    {"UserName": "idle", "Client": "Jellyfin Web", "DeviceName": "Chrome"},
]
SEEN = []


def handler(server):
    async def users(request):
        return web.json_response(USERS[server])

    async def items(request):
        if server in DOWN:
            return web.Response(status=503)
        user = request.match_info.get("user")
        SEEN.append((server, user, request.query.get("EnableUserData")))
        out = []
        for name, year, tmdb, played in LIBRARIES[server]:
            item = {"Name": name, "ProductionYear": year, "ProviderIds": {"Tmdb": tmdb} if tmdb else {}}
            if user:
                item["UserData"] = {"Played": played}
            out.append(item)
        return web.json_response({"Items": out})
    async def sessions(request):
        if server in DOWN:
            return web.Response(status=503)
        SEEN.append((server, "sessions", request.query.get("activeWithinSeconds")))
        return web.json_response(SESSIONS if server == "home" else [])
    return users, items, sessions


class Interaction:
    def __init__(s, uid=1):
        s.user = types.SimpleNamespace(id=uid, display_name="tester", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png"))
        s.sent = []
        s.response = types.SimpleNamespace(defer=s._defer, send_message=s._send, edit_message=s._edit, is_done=lambda: True)
        s.followup = types.SimpleNamespace(send=s._send)
    async def _defer(s, **k): s.deferred = k
    async def _send(s, content=None, **k):
        s.sent.append((content, k.get("embed"), k.get("view"), k.get("ephemeral")))
        return types.SimpleNamespace(id=1, flags=types.SimpleNamespace(ephemeral=True), channel=None)
    async def _edit(s, **k): s.sent.append((None, k.get("embed"), k.get("view"), None))


def body(embed):
    return [l for l in embed.description.split("\n")]


async def run(title):
    i = Interaction()
    await J.JellyfinCog().movies.callback(J.JellyfinCog(), i, title)
    return i


def titles(i):
    return [l.split("**")[1] for l in body(i.sent[-1][1]) if " — " in l]


async def main():
    app = web.Application()
    for server in ("home", "friend"):
        users, items, sessions = handler(server)
        app.add_routes([web.get(f"/{server}/Sessions", sessions), web.get(f"/{server}/Users", users), web.get(f"/{server}/Items", items),
                        web.get(f"/{server}/Users/{{user}}/Items", items)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== one title ==")
        i = await run("arrival")
        content, embed, view, private = i.sent[-1]
        for line in body(embed):
            print("  |", line)
        print("  footer:", embed.footer.text)
        print("  private:", private, "| asked per user:", sorted(set((s, u) for s, u, _ in SEEN)), "| view message set:", view.message is not None)

        print("\n== search ==")
        print("  'dune', exact first:", titles(await run("dune")))
        print("  'THE thing':", titles(await run("THE thing")))
        print("  no match:", body((await run("zzz")).sent[-1][1])[2])

        print("\n== autocomplete ==")
        cog = J.JellyfinCog()
        choices = await cog.movie_titles(Interaction(), "dun")
        print("  'dun':", [(c.name, c.value) for c in choices])
        print("  empty:", await cog.movie_titles(Interaction(), " "))

        print("\n== no user set for Friend ==")
        del os.environ["JELLYFIN_2_USER"]
        i = await run("ronin")
        lines = body(i.sent[-1][1])
        print("  ", [l for l in lines if l.startswith("**Ronin")][0], "|", lines[-1], "|", i.sent[-1][1].footer.text)
        print("   explained:", [l for l in lines if "JELLYFIN_*_USER" in l])
        os.environ["JELLYFIN_2_USER"] = "nobody"
        i = await run("ronin")
        print("   wrong name:", [l for l in body(i.sent[-1][1]) if "JELLYFIN_*_USER" in l])
        os.environ["JELLYFIN_2_USER"] = "alex"

        print("\n== a server down ==")
        DOWN.add("friend")
        i = await run("arrival")
        lines = body(i.sent[-1][1])
        print("  ", lines[1], "|", [l for l in lines if " — " in l])
        DOWN.add("home")
        i = await run("arrival")
        print("   both down:", i.sent[-1][0])
        DOWN.clear()

        print("\n== paging ==")
        LIBRARIES["home"] += [(f"Film {n:02}", 2000, f"9{n:03}", n % 2 == 0) for n in range(30)]
        i = await run("film")
        embed, view = i.sent[-1][1], i.sent[-1][2]
        print("  30 matches:", embed.footer.text.split(" • ")[0], "| buttons:", [(b.label, b.disabled) for b in view.children])
        other = Interaction(uid=2)
        print("  someone else pages:", await view.interaction_check(other), other.sent[-1][0])
        press = Interaction()
        await view.next_button.callback(press)
        print("  after Next:", press.sent[-1][1].footer.text.split(" • ")[0], "| Next disabled:", view.next_button.disabled)

        print("\n== now playing ==")
        cog = J.JellyfinCog()
        admin = Interaction(); admin.user.guild_permissions = types.SimpleNamespace(administrator=True)
        await cog.playing.callback(cog, admin)
        embed = admin.sent[-1][1]
        print("  title:", embed.title, "| private:", admin.sent[-1][3])
        for line in embed.description.split("\n"):
            print("  |", line)
        print("  only your own server asked:", sorted({s for s, u, _ in SEEN if u == "sessions"}))
        member = Interaction(); member.user.guild_permissions = types.SimpleNamespace(administrator=False)
        await cog.playing.callback(cog, member)
        print("  member:", member.sent[-1][0])
        SESSIONS.clear(); quiet = Interaction(); quiet.user.guild_permissions = admin.user.guild_permissions
        await cog.playing.callback(cog, quiet)
        print("  nothing playing:", quiet.sent[-1][1].description)
        DOWN.add("home"); down = Interaction(); down.user.guild_permissions = admin.user.guild_permissions
        await cog.playing.callback(cog, down)
        print("  server down:", down.sent[-1][0]); DOWN.clear()

        print("\n== matching ==")
        merged = ML.combine([("A", [{"Name": "Blade Runner", "ProductionYear": 1982, "ProviderIds": {}}], False),
                             ("B", [{"Name": "Blade Runner", "ProductionYear": 1982, "ProviderIds": {"Tmdb": "78"}},
                                    {"Name": "Blade Runner", "ProductionYear": 2017, "ProviderIds": {}}], False)])
        print("  title+year joins, other year stays apart:", [(m["title"], m["year"], sorted(m["servers"])) for m in merged])
    finally:
        await runner.cleanup()

asyncio.run(main())
