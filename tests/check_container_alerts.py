"""Container alerts through a local fake Portainer: crashes, unhealthy and restarting containers alert once and recover once; normal stops, ignored containers and old exits stay quiet."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18120
for key in [k for k in os.environ if k.startswith(("PORTAINER_", "CONTAINER_ALERTS"))]:
    del os.environ[key]
os.environ.update({"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "k", "PORTAINER_ENDPOINT_ID": "1",
                   "CONTAINER_ALERTS_IGNORE": "ente-minio-init"})

import modules.containers.container_monitor as CM
import isolate  # state files go to a temporary folder
CM.ContainerMonitor.STATE_FILE = isolate.DATA / "container_monitor.json"

# name: (state, status)
BOXES = {}
DOWN = {"portainer": False}


async def docker(request):
    if DOWN["portainer"]:
        return web.Response(status=502)
    return web.json_response([{"Id": n * 4, "Names": [f"/{n}"], "Image": "x", "State": st, "Status": status}
                              for n, (st, status) in BOXES.items()])


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title))
        return send


async def main():
    app = web.Application(); app.add_routes([web.get("/api/endpoints/1/docker/containers/json", docker)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        notes = Notes(); mon = CM.ContainerMonitor(notes)

        async def step(label, **changes):
            BOXES.update(changes)
            before = len(notes.sent)
            await mon.check()
            print(f"  {label:44} -> {notes.sent[before:]}")

        print("== first check ==")
        BOXES.update({"jellyfin": ("running", "Up 3 days (healthy)"), "retrom": ("exited", "Exited (1) 2 weeks ago"),
                      "sonarr": ("running", "Up 1 hour (unhealthy)"), "ente-minio-init": ("exited", "Exited (2) 1 day ago"),
                      "minecraft": ("exited", "Exited (0) 4 hours ago")})
        await step("old crash recorded, unhealthy reported")

        print("\n== changes ==")
        await step("nothing changed")
        await step("jellyfin crashes", jellyfin=("exited", "Exited (139) 5 seconds ago"))
        await step("still crashed", jellyfin=("exited", "Exited (139) 1 minute ago"))
        await step("jellyfin back", jellyfin=("running", "Up 10 seconds"))
        await step("sonarr healthy again", sonarr=("running", "Up 1 hour (healthy)"))
        await step("crash loop", qbittorrentvpn=("restarting", "Restarting (1) 3 seconds ago"))
        await step("normal stop by docker stop (143)", minecraft=("running", "Up 1 minute"))
        await step("", minecraft=("exited", "Exited (143) 2 seconds ago"))
        await step("stop that ran out of grace (137)", jellyfin=("exited", "Exited (137) 1 second ago"))
        await step("ignored container exits badly", **{"ente-minio-init": ("exited", "Exited (1) now")})
        BOXES.pop("qbittorrentvpn"); before = len(notes.sent); await mon.check()
        print(f"  {'restarting container removed':44} -> {notes.sent[before:]} | forgotten:", "qbittorrentvpn" not in mon.state["containers"])
        DOWN["portainer"] = True
        await step("Portainer not answering")
        DOWN["portainer"] = False

        print("\n== after a bot restart ==")
        again = CM.ContainerMonitor(notes); before = len(notes.sent); await again.check()
        print("  state kept, no repeats ->", notes.sent[before:])
        os.environ["CONTAINER_ALERTS"] = "false"; before = len(notes.sent)
        BOXES["retrom"] = ("running", "Up 1 second"); await again.check()
        print("  CONTAINER_ALERTS=false ->", notes.sent[before:])
    finally:
        await runner.cleanup()

asyncio.run(main())
