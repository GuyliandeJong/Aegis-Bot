"""No-peers watchdog against a fake qBittorrent and Portainer: counting, the alert, the restart, the grace period, the restart limit, recovery, and every case where it must stay quiet."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("aegis").setLevel(logging.ERROR)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18190
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="127.0.0.1", QBITTORRENT_PORT=PORT, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", ALERT_COOLDOWN=0, DISCORD_USER_ID="")
sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith(("PORTAINER_", "QBITTORRENT_NO_PEERS"))]:
    del os.environ[key]
PORTAINER = {"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "k", "PORTAINER_ENDPOINT_ID": "1"}
os.environ.update(PORTAINER)
os.environ["QBITTORRENT_NO_PEERS_MINUTES"] = "10"

import modules.torrents.peers_monitor as NP
from modules.torrents.qbittorrent_service import QBittorrentService
import isolate  # state files go to a temporary folder

# What the fake qBittorrent answers. "sid" is the cookie it accepts; a
# restart changes it, so the bot's old session gets one 403.
QBT = {"nodes": 300, "dht": True, "sid": "abc", "down": False, "fields": True}
REQUESTS = []
POSTS = []
CLOCK = [1_000_000.0]
NAME = "q" * 64


async def login(request):
    resp = web.Response(text="Ok.")
    resp.set_cookie("SID", QBT["sid"])
    return resp


async def transfer_info(request):
    REQUESTS.append("transfer")
    if QBT["down"]:
        return web.Response(status=500, text="starting")
    if request.cookies.get("SID") != QBT["sid"]:
        return web.Response(status=403, text="Forbidden")
    data = {"dl_info_speed": 0, "connection_status": "firewalled"}
    if QBT["fields"]:
        data["dht_nodes"] = QBT["nodes"]
    return web.json_response(data)


async def preferences(request):
    REQUESTS.append("preferences")
    if QBT["dht"] is None:
        return web.json_response({"listen_port": 1})
    return web.json_response({"dht": QBT["dht"]})


async def docker(request):
    path = request.match_info["path"]
    if path == "containers/json":
        return web.json_response([{"Id": NAME, "Names": ["/qbittorrentvpn"], "Image": "example/qbittorrent", "State": "running", "Status": "Up 3 days"}])
    POSTS.append(path.split("/")[-1])
    QBT["sid"] = f"after-restart-{len(POSTS)}"
    return web.Response(status=204)


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send

    def titles(s): return [t for _, t, _ in s.sent]


_files = [0]


def monitor(notes):
    mon = NP.NoPeersMonitor(notes)
    mon._now = lambda: CLOCK[0]
    return mon


def fresh_file():
    _files[0] += 1
    NP.NoPeersMonitor.STATE_FILE = isolate.DATA / f"no-peers-{_files[0]}.json"


async def run(mon, count):
    for _ in range(count):
        await mon.check()
    if NP._tasks:
        await asyncio.gather(*list(NP._tasks))


def reset(nodes=0, dht=True):
    QBT.update(nodes=nodes, dht=dht, down=False, fields=True)
    REQUESTS.clear(); POSTS.clear()


async def main():
    app = web.Application()
    app.add_routes([web.post("/api/v2/auth/login", login), web.get("/api/v2/transfer/info", transfer_info),
                    web.get("/api/v2/app/preferences", preferences),
                    web.route("*", "/api/endpoints/1/docker/{path:.*}", docker)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== healthy and below the threshold ==")
        fresh_file(); notes = Notes(); mon = monitor(notes)
        reset(nodes=300)
        await run(mon, 8)
        print("  nodes above 0, 8 checks -> alerts:", notes.titles(), "| preferences fetched:", REQUESTS.count("preferences"))
        reset(nodes=0)
        await run(mon, 4)
        print("  4 of 5 checks at 0      -> alerts:", notes.titles(), "| restarts:", POSTS)

        print("\n== alert only (no container set) ==")
        await run(mon, 1)
        print("  5th check               ->", [(lvl, t) for lvl, t, _ in notes.sent], "| restarts:", POSTS)
        print("  text:", notes.sent[0][2].replace("\n", " / "))
        await run(mon, 6)
        print("  6 more checks           -> alerts in total:", len(notes.sent))
        QBT["nodes"] = 12
        await run(mon, 1)
        print("  nodes back              ->", notes.titles()[1:])
        await run(mon, 1)
        print("  and again               -> alerts in total:", len(notes.sent))

        print("\n== a counter that survives a restart of the bot ==")
        fresh_file(); notes = Notes(); mon = monitor(notes)
        reset(nodes=0)
        await run(mon, 3)
        QBT["down"] = True
        await run(mon, 4)
        QBT["down"] = False
        print("  3 checks, 4 unreachable -> zero count:", mon.state["zero"])
        mon = monitor(notes)
        await run(mon, 1)
        print("  3 checks, new instance, 1 more -> alerts:", len(notes.sent))
        await run(mon, 1)
        mon = monitor(notes)
        await run(mon, 3)
        print("  alerted, new instance, 3 more  -> alerts in total:", len(notes.sent))

        print("\n== restart, grace period, limit ==")
        os.environ["QBITTORRENT_NO_PEERS_RESTART"] = "qbittorrentvpn"
        fresh_file(); notes = Notes(); mon = monitor(notes)
        reset(nodes=0)
        await run(mon, 5)
        print("  threshold               ->", notes.titles(), "| restarts:", POSTS)
        print("  text:", notes.sent[0][2].replace("\n", " / "))
        QBT["down"] = True
        await run(mon, 6)
        print("  unreachable for 6 checks -> restarts:", POSTS, "| zero count:", mon.state["zero"])
        QBT["down"] = False
        notes.sent.clear()
        await run(mon, 12)
        print("  12 checks inside the grace period -> restarts:", POSTS, "| alerts:", notes.titles())
        CLOCK[0] += 601
        await run(mon, 5)
        print("  after the grace period  -> restarts:", POSTS, "| alerts:", notes.titles())
        CLOCK[0] += 601
        notes.sent.clear()
        await run(mon, 5)
        print("  limit reached           ->", notes.titles(), "| restarts:", POSTS)
        print("  text:", notes.sent[0][2].replace("\n", " / "))
        await run(mon, 10)
        print("  10 more checks          -> alerts in total:", len(notes.sent), "| restarts:", POSTS)
        QBT["nodes"] = 40
        notes.sent.clear()
        await run(mon, 1)
        print("  peers back              ->", [(lvl, t) for lvl, t, _ in notes.sent])
        print("  state:", {k: mon.state[k] for k in ("zero", "alerted", "gave_up")}, "| restarts kept:", len(mon.state["restarts"]))

        print("\n== a restart that fails ==")
        os.environ["PORTAINER_PROTECTED"] = "qbittorrentvpn"
        fresh_file(); notes = Notes(); mon = monitor(notes)
        reset(nodes=0); CLOCK[0] += 10 * 3600
        await run(mon, 5)
        print("  protected container     ->", notes.titles(), "| restarts:", POSTS)
        print("  text:", notes.sent[-1][2].replace("\n", " / ")[:80])
        del os.environ["PORTAINER_PROTECTED"]

        print("\n== no Portainer ==")
        for key in PORTAINER:
            del os.environ[key]
        fresh_file(); notes = Notes(); mon = monitor(notes)
        reset(nodes=0)
        await run(mon, 5)
        print("  ", [(lvl, t) for lvl, t, _ in notes.sent], "| restarts:", POSTS)
        print("  text:", notes.sent[0][2].replace("\n", " / ")[-48:])
        os.environ.update(PORTAINER)
        del os.environ["QBITTORRENT_NO_PEERS_RESTART"]

        print("\n== stays quiet ==")
        fresh_file(); notes = Notes(); mon = monitor(notes)
        reset(nodes=0, dht=False)
        await run(mon, 10)
        print("  DHT turned off in qBittorrent -> alerts:", notes.titles())
        reset(nodes=0, dht=None)
        await run(mon, 10)
        print("  preferences without a dht key -> alerts:", notes.titles())
        reset(nodes=0); QBT["fields"] = False
        await run(mon, 10)
        print("  no dht_nodes in the answer    -> alerts:", notes.titles(), "| preferences fetched:", REQUESTS.count("preferences"))
        reset(nodes=0)
        os.environ["QBITTORRENT_NO_PEERS_MINUTES"] = "0"
        await run(mon, 10)
        print("  QBITTORRENT_NO_PEERS_MINUTES=0 -> requests:", REQUESTS, "| alerts:", notes.titles())
        os.environ["QBITTORRENT_NO_PEERS_MINUTES"] = "10"
        c.settings.QBITTORRENT_HOST = ""
        await run(mon, 10)
        print("  qBittorrent not configured    -> requests:", REQUESTS)
        c.settings.QBITTORRENT_HOST = "127.0.0.1"
        os.environ["QBITTORRENT_NO_PEERS_MINUTES"] = "3"
        print("  3 minutes -> checks needed:", mon.checks_needed())
    finally:
        await QBittorrentService.shutdown()
        await runner.cleanup()

asyncio.run(main())
