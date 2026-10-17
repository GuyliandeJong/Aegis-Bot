"""Restarting containers such as newt after an internet outage: when it applies, the Portainer request, protection, the posted result, and the weekly summary line."""
import asyncio
import logging
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(WAN_OUTAGE_DURATION=300, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18117
for key in [k for k in os.environ if k.startswith(("PORTAINER_", "WAN_RECOVERY_"))]:
    del os.environ[key]
os.environ.update({"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "k", "PORTAINER_ENDPOINT_ID": "1",
                   "WAN_RECOVERY_DELAY": "0"})

import monitors.wan_monitor as WM
import monitors.weekly_summary_monitor as WS
import services.wan_recovery as WR
from monitors.state import state
import isolate  # state files go to a temporary folder
state.save = lambda: None

CONTAINERS = {"newt": "n" * 64, "jellyfin": "j" * 64}
POSTS = []


async def docker(request):
    path = request.match_info["path"]
    if path == "containers/json":
        return web.json_response([{"Id": i, "Names": [f"/{n}"], "Image": "fosrl/newt", "State": "running", "Status": "Up 3 days"}
                                  for n, i in CONTAINERS.items()])
    POSTS.append(path.split("/")[-1] + " " + {v: k for k, v in CONTAINERS.items()}.get(path.split("/")[1], "?"))
    return web.Response(status=204)


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


class Wan:
    def __init__(s): s.up = True
    async def is_connected(s): return s.up
    async def get_ip(s): return "203.0.113.5"


async def settle():
    await asyncio.gather(*list(WR._tasks))


async def outage(seconds, confirmed):
    """Simulate an outage of `seconds`, then a check that sees the internet again."""
    notes = Notes(); mon = WM.WanMonitor(notes); mon.service = Wan()
    start = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    state.wan = {"connected": False if confirmed else True, "outage_since": start.isoformat(), "ip": "203.0.113.5"}
    mon._now = staticmethod(lambda: start + timedelta(seconds=seconds))
    POSTS.clear()
    await mon.check()
    await settle()
    return notes.sent


async def main():
    app = web.Application(); app.add_routes([web.route("*", "/api/endpoints/1/docker/{path:.*}", docker)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== not configured ==")
        sent = await outage(600, True)
        print("  reported:", [s[1] for s in sent], "| restarts:", POSTS)

        os.environ["WAN_RECOVERY_RESTART"] = "newt"
        print("\n== confirmed outage of 10 minutes ==")
        sent = await outage(600, True)
        print("  reported:", [s[1] for s in sent], "| restarts:", POSTS)
        print("  message:", sent[-1][2].replace("\n", " / "))

        print("\n== short outage ==")
        sent = await outage(90, False)
        print("  90s, not alerted -> reported:", [s[1] for s in sent], "| restarts:", POSTS)
        sent = await outage(20, False)
        print("  20s, below WAN_RECOVERY_MIN_OUTAGE -> restarts:", POSTS)

        print("\n== no outage ==")
        notes = Notes(); mon = WM.WanMonitor(notes); mon.service = Wan()
        state.wan = {"connected": True, "outage_since": None, "ip": "203.0.113.5"}; POSTS.clear()
        await mon.check(); await settle()
        print("  restarts:", POSTS)

        print("\n== problems ==")
        os.environ["WAN_RECOVERY_RESTART"] = "newt, missing"
        sent = await outage(600, True)
        print("  one missing:", sent[-1][1], "| restarts:", POSTS, "|", sent[-1][2].split(" / ")[0].replace("\n", " / "))
        os.environ["WAN_RECOVERY_RESTART"] = "newt"; os.environ["PORTAINER_PROTECTED"] = "newt"
        sent = await outage(600, True)
        print("  protected:", sent[-1][1], "| restarts:", POSTS)
        del os.environ["PORTAINER_PROTECTED"]

        print("\n== weekly summary ==")
        print("  ", WS.WeeklySummaryMonitor._outages(time.time() - 3600).replace("\n", " / "))
    finally:
        await runner.cleanup()

asyncio.run(main())
