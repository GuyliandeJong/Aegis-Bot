"""Images with updates against a fake Portainer: a current image, an outdated one, one image shared by two containers, images built locally or pinned by digest, a registry that does not answer, stopped containers ignored, and the weekly summary field (text, off, not configured, nothing comparable)."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18204
for key in [k for k in os.environ if k.startswith(("PORTAINER_", "WEEKLY_SUMMARY"))]:
    del os.environ[key]
os.environ.update({"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "k", "PORTAINER_ENDPOINT_ID": "1"})

from modules.containers import image_updates as IU
from modules.containers.portainer_service import PortainerService
import modules.general.weekly_summary_monitor as WS
import isolate  # state files go to a temporary folder
WS.WeeklySummaryMonitor.STATE_FILE = isolate.DATA / "weekly.json"

# image -> (image id, digests it was pulled as, digest the registry has)
IMAGES = {
    "jellyfin/jellyfin:latest": ("sha256:" + "1" * 64, ["jellyfin/jellyfin@sha256:old1"], "sha256:new1"),
    "ghcr.io/example/tool:1.2": ("sha256:" + "2" * 64, ["ghcr.io/example/tool@sha256:same2"], "sha256:same2"),
    "linuxserver/sonarr:latest": ("sha256:" + "3" * 64, ["linuxserver/sonarr@sha256:old3"], "sha256:new3"),
    "aegis-bot:latest": ("sha256:" + "4" * 64, [], None),
    "example/broken:latest": ("sha256:" + "5" * 64, ["example/broken@sha256:x5"], None),
    "example/pinned@sha256:" + "6" * 64: ("sha256:" + "6" * 64, ["example/pinned@sha256:" + "6" * 64], "sha256:" + "6" * 64),
    "example/stopped:latest": ("sha256:" + "7" * 64, ["example/stopped@sha256:old7"], "sha256:new7"),
}
CONTAINERS = [
    ("jellyfin", "jellyfin/jellyfin:latest", "running"), ("jellyfin-copy", "jellyfin/jellyfin:latest", "running"),
    ("tool", "ghcr.io/example/tool:1.2", "running"), ("sonarr", "linuxserver/sonarr:latest", "running"),
    ("aegis-bot", "aegis-bot:latest", "running"), ("broken", "example/broken:latest", "running"),
    ("pinned", "example/pinned@sha256:" + "6" * 64, "running"), ("stopped", "example/stopped:latest", "exited"),
]
ASKED = []


async def containers(request):
    return web.json_response([{"Id": f"{i:064x}", "Names": [f"/{name}"], "Image": image, "ImageID": IMAGES[image][0], "State": state,
                               "Status": "Up 3 days" if state == "running" else "Exited (0) 2 days ago"} for i, (name, image, state) in enumerate(CONTAINERS)])


async def image_json(request):
    image_id = request.match_info["id"]
    for image, (known, digests, _) in IMAGES.items():
        if known == image_id:
            return web.json_response({"Id": known, "RepoDigests": digests})
    return web.Response(status=404, text='{"message": "no such image"}')


async def distribution(request):
    name = request.match_info["name"]
    ASKED.append(name)
    entry = IMAGES.get(name)
    if entry is None or entry[2] is None:
        return web.Response(status=500, text='{"message": "registry unreachable"}')
    return web.json_response({"Descriptor": {"digest": entry[2]}})


async def main():
    app = web.Application()
    app.add_routes([web.get("/api/endpoints/1/docker/containers/json", containers),
                    web.get("/api/endpoints/1/docker/images/{id}/json", image_json),
                    web.get("/api/endpoints/1/docker/distribution/{name:.+}/json", distribution)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== the comparison ==")
        found = await IU.outdated(PortainerService())
        print("  outdated:", found["outdated"])
        print("  checked:", found["checked"], "| skipped:", found["skipped"])
        print("  registry asked once per image:", sorted(ASKED))

        print("\n== the weekly summary field ==")
        mon = WS.WeeklySummaryMonitor(types.SimpleNamespace())
        text = await mon._image_updates(0)
        print("  text:", text.replace("\n", " / "))
        print("  field listed for the containers module:", ("containers", "📦 Images with updates", "_image_updates") in WS.WeeklySummaryMonitor.FIELDS)

        print("\n== off, and nothing to compare ==")
        os.environ["WEEKLY_SUMMARY_IMAGE_UPDATES"] = "false"
        ASKED.clear()
        print("  WEEKLY_SUMMARY_IMAGE_UPDATES=false ->", await mon._image_updates(0), "| asked:", ASKED)
        del os.environ["WEEKLY_SUMMARY_IMAGE_UPDATES"]
        saved = os.environ.pop("PORTAINER_URL")
        print("  Portainer not configured ->", await mon._image_updates(0))
        os.environ["PORTAINER_URL"] = saved
        for key in ("jellyfin/jellyfin:latest", "linuxserver/sonarr:latest"):
            IMAGES[key] = (IMAGES[key][0], IMAGES[key][1], IMAGES[key][1][0].split("@")[1])
        print("  everything current ->", await mon._image_updates(0))
        for key in IMAGES:
            IMAGES[key] = (IMAGES[key][0], [], IMAGES[key][2])
        print("  nothing pulled from a registry ->", await mon._image_updates(0))
    finally:
        await runner.cleanup()

asyncio.run(main())
