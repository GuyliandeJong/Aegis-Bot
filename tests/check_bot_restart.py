"""/bot restart against a fake Portainer: only allowed users, Portainer must be set up, a confirmation only the same person can press, the restart request to the bot's own container (also when it is protected), nothing sent on cancel, and /container still refusing the bot's own container."""
import asyncio
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
PORT = 18202
for key in [k for k in os.environ if k.startswith("PORTAINER_")]:
    del os.environ[key]
OWN = "ab12cd34ef56" + "0" * 52
OTHER = "ff" * 32
os.environ.update({"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "k", "PORTAINER_ENDPOINT_ID": "1",
                   "PORTAINER_ALLOWED_USER_IDS": "42", "HOSTNAME": OWN[:12]})

import discord
from core import activity_log
import modules.containers.commands.bot as BC
import modules.containers.portainer_service as PS
from modules.containers.portainer_service import PortainerError, PortainerService
import isolate  # state files go to a temporary folder

# What the bot reads about its own container; inside the real container
# the real files would otherwise decide.
MOUNTS = Path(tempfile.mkdtemp(prefix="mountinfo-", dir=str(isolate.DATA))) / "mountinfo"
PS.MOUNTINFO = MOUNTS / "missing"


def mountinfo(*lines):
    MOUNTS.parent.mkdir(exist_ok=True)
    MOUNTS.write_text("\n".join(lines) + "\n")
    PS.MOUNTINFO = MOUNTS


def mount_lines(container_id, root="/var/lib/docker"):
    return [
        f"1001 990 8:1 {root}/containers/{container_id}/resolv.conf /etc/resolv.conf rw,relatime - ext4 /dev/sda1 rw",
        f"1002 990 8:1 {root}/containers/{container_id}/hostname /etc/hostname rw,relatime - ext4 /dev/sda1 rw",
        f"1003 990 8:1 {root}/containers/{container_id}/hosts /etc/hosts rw,relatime - ext4 /dev/sda1 rw",
        "1004 990 8:1 /home/user/Aegis-Bot /app rw,relatime - ext4 /dev/sda1 rw",
    ]

POSTS = []
FAIL = {"restart": False}
NAMES = {"own": "aegis-bot"}


async def docker(request):
    path = request.match_info["path"]
    if path == "containers/json":
        return web.json_response([
            {"Id": OWN, "Names": [f"/{NAMES['own']}"], "Image": "aegis-bot:latest", "State": "running", "Status": "Up 3 days"},
            {"Id": OTHER, "Names": ["/jellyfin"], "Image": "jellyfin/jellyfin", "State": "running", "Status": "Up 3 days"},
        ])
    if FAIL["restart"]:
        return web.Response(status=500, text='{"message": "boom"}')
    POSTS.append(path)
    return web.Response(status=204)


class Interaction:
    def __init__(self, uid=42):
        self.user = types.SimpleNamespace(id=uid, display_name="t", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png"))
        self.log = []
        self.response = types.SimpleNamespace(send_message=self._send, edit_message=self._edit)
    async def _send(self, content=None, **k): self.log.append(("send", content, k))
    async def _edit(self, **k): self.log.append(("edit", k.get("content"), k))
    async def edit_original_response(self, **k): self.log.append(("original", None, k))


async def settle():
    if BC._tasks:
        await asyncio.gather(*list(BC._tasks))


async def main():
    app = web.Application(); app.add_routes([web.route("*", "/api/endpoints/1/docker/{path:.*}", docker)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    cog = BC.BotCog(types.SimpleNamespace())
    try:
        print("== who may ==")
        print("  administrators only by default:", BC.BotCog.control_group.default_permissions.administrator)
        i = Interaction(uid=7)
        await cog.restart.callback(cog, i)
        print("  not in PORTAINER_ALLOWED_USER_IDS ->", i.log[0][1], "| buttons:", "view" in i.log[0][2])
        saved = os.environ.pop("PORTAINER_URL")
        i = Interaction()
        await cog.restart.callback(cog, i)
        print("  Portainer not configured ->", i.log[0][1][:40])
        os.environ["PORTAINER_URL"] = saved

        print("\n== the confirmation ==")
        i = Interaction()
        await cog.restart.callback(cog, i)
        k = i.log[0][2]
        print("  question:", k["embed"].title.split(" ", 1)[1], "| private:", k["ephemeral"], "| buttons:", [b.label for b in k["view"].children])
        view = k["view"]
        print("  someone else may press:", await view.interaction_check(Interaction(uid=7)))
        print("  the same person may press:", await view.interaction_check(Interaction()))
        press = Interaction()
        await view.children[1].callback(press)
        await settle()
        print("  Cancel ->", press.log[0][1], "| requests:", POSTS)

        print("\n== restart now ==")
        i = Interaction(); await cog.restart.callback(cog, i)
        view = i.log[0][2]["view"]
        os.environ["PORTAINER_PROTECTED"] = "aegis-bot"
        press = Interaction()
        before = len(activity_log.since(0, "bot_restart"))
        await view.children[0].callback(press)
        await settle()
        print("  reply ->", press.log[0][1])
        print("  request ->", POSTS, "| to the bot's own container:", POSTS == [f"containers/{OWN}/restart"])
        print("  protected by name, still restarted:", "aegis-bot" in PortainerService().protected)
        print("  written to the activity log:", len(activity_log.since(0, "bot_restart")) - before)
        del os.environ["PORTAINER_PROTECTED"]

        print("\n== /container still refuses it ==")
        service = PortainerService()
        own = await service.own_container()
        try:
            await service.perform("restart", own)
            print("  perform ->", "allowed")
        except PortainerError as ex:
            print("  perform ->", ex)
        POSTS.clear()
        other = await service.find_container("jellyfin")
        print("  another container ->", await service.perform("restart", other), POSTS)

        print("\n== recognising the bot's own container ==")
        service = PortainerService()
        os.environ["HOSTNAME"] = "fs01-linux"
        NAMES["own"] = "renamed-bot"
        mountinfo(*mount_lines(OWN))
        print("  ids read from the mounts:", [i[:12] for i in service.own_ids()])
        mountinfo(*mount_lines(OWN, root=""))
        print("  the same when the data root is its own mount:", [i[:12] for i in service.own_ids()])
        mountinfo(*mount_lines(OWN))
        own = await service.own_container()
        print("  custom hostname, renamed, id in the mounts ->", own["name"], "| protected:", service.protection_reason(own))
        print("  another container is not the bot's:", service.protection_reason(await service.find_container("jellyfin")))
        mountinfo(*mount_lines("ee" * 32))
        NAMES["own"] = "aegis-bot"
        own = await service.own_container()
        print("  mounts name an unknown id, falls through to the name ->", own["name"])
        PS.MOUNTINFO = MOUNTS / "missing"
        os.environ["HOSTNAME"] = OWN[:12]
        NAMES["own"] = "renamed-bot"
        print("  no mounts, default hostname ->", (await service.own_container())["name"])
        os.environ["HOSTNAME"] = "fs01-linux"
        NAMES["own"] = "aegis-bot"
        own = await service.own_container()
        print("  no mounts, custom hostname, named aegis-bot ->", own["name"], "| protected:", service.protection_reason(own))
        try:
            await service.perform("restart", own)
        except PortainerError as ex:
            print("  /container refuses it:", ex)
        NAMES["own"] = "renamed-bot"
        try:
            await service.own_container()
        except PortainerError as ex:
            print("  nothing matches ->", str(ex)[:90])

        print("\n== when it cannot work ==")
        os.environ["HOSTNAME"] = "elsewhere"
        POSTS.clear()
        i = Interaction(); await cog.restart.callback(cog, i)
        press = Interaction(); await i.log[0][2]["view"].children[0].callback(press); await settle()
        print("  unknown own container ->", press.log[0][1][:62], "| requests:", POSTS)
        os.environ["HOSTNAME"] = OWN[:12]
        NAMES["own"] = "aegis-bot"
        FAIL["restart"] = True
        i = Interaction(); await cog.restart.callback(cog, i)
        press = Interaction(); await i.log[0][2]["view"].children[0].callback(press); await settle()
        print("  Portainer refuses -> reply still sent:", press.log[0][1][:20], "| no crash")
    finally:
        await runner.cleanup()

asyncio.run(main())
