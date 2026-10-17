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

import commands.jellyfin as J
from services import movie_library as ML
import isolate  # state files go to a temporary folder

# (name, year, tmdb, played)
LIBRARIES = {
    "home": [("Arrival", 2016, "329865", True), ("Dune", 2021, "438631", False), ("Heat", 1995, "", False),
             ("The Thing", 1982, "1091", False)],
    "friend": [("Arrival", 2016, "329865", False), ("Dune", 2021, "438631", True), ("Heat", 1995, "", False),
               ("Ronin", 1998, "8195", False), ("Alien", 1979, "348", True)],
}
USERS = {"home": [{"Name": "Alex", "Id": "u-home"}], "friend": [{"Name": "Friend", "Id": "x"}, {"Name": "Alex", "Id": "u-friend"}]}
DOWN = set()
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
    return users, items


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


async def run(show="all", search=""):
    i = Interaction()
    await J.JellyfinCog().movies.callback(J.JellyfinCog(), i, show, search)
    return i


async def main():
    app = web.Application()
    for server in ("home", "friend"):
        users, items = handler(server)
        app.add_routes([web.get(f"/{server}/Users", users), web.get(f"/{server}/Items", items),
                        web.get(f"/{server}/Users/{{user}}/Items", items)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== all movies ==")
        i = await run()
        content, embed, view, private = i.sent[-1]
        for line in body(embed):
            print("  |", line)
        print("  footer:", embed.footer.text)
        print("  private:", private, "| asked per user:", sorted(set((s, u) for s, u, _ in SEEN)), "| view message set:", view.message is not None)

        print("\n== filters ==")
        for show in ("unwatched", "watched", "not_mine"):
            i = await run(show)
            titles = [l.split("**")[1] for l in body(i.sent[-1][1]) if " — " in l]
            print(f"  {show:10}", titles)
        i = await run("all", "ali")
        print("  search 'ali':", [l.split("**")[1] for l in body(i.sent[-1][1]) if " — " in l], "|", body(i.sent[-1][1])[1])

        print("\n== no user set for Friend ==")
        del os.environ["JELLYFIN_2_USER"]
        i = await run()
        lines = body(i.sent[-1][1])
        print("  ", [l for l in lines if l.startswith("**Ronin")][0], "|", lines[-1], "|", i.sent[-1][1].footer.text)
        os.environ["JELLYFIN_2_USER"] = "alex"

        print("\n== a server down ==")
        DOWN.add("friend")
        i = await run()
        lines = body(i.sent[-1][1])
        print("  ", lines[1], "| movies:", lines[0])
        DOWN.add("home")
        i = await run()
        print("   both down:", i.sent[-1][0])
        DOWN.clear()

        print("\n== paging ==")
        LIBRARIES["home"] += [(f"Film {n:02}", 2000, f"9{n:03}", n % 2 == 0) for n in range(30)]
        i = await run()
        embed, view = i.sent[-1][1], i.sent[-1][2]
        print("  page 1:", embed.footer.text.split(" • ")[1], "| buttons:", [(b.label, b.disabled) for b in view.children])
        other = Interaction(uid=2)
        print("  someone else pages:", await view.interaction_check(other), other.sent[-1][0])
        press = Interaction()
        await view.next_button.callback(press)
        print("  after Next:", press.sent[-1][1].footer.text.split(" • ")[1], "| Next disabled:", view.next_button.disabled)

        print("\n== matching ==")
        merged = ML.combine([("A", [{"Name": "Blade Runner", "ProductionYear": 1982, "ProviderIds": {}}], False),
                             ("B", [{"Name": "Blade Runner", "ProductionYear": 1982, "ProviderIds": {"Tmdb": "78"}},
                                    {"Name": "Blade Runner", "ProductionYear": 2017, "ProviderIds": {}}], False)])
        print("  title+year joins, other year stays apart:", [(m["title"], m["year"], sorted(m["servers"])) for m in merged])
    finally:
        await runner.cleanup()

asyncio.run(main())
