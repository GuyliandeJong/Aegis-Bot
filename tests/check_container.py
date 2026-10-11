"""/container against a local fake Portainer: access, listing, protection, the confirmation flows for stop, kill and remove, logs, and the requests Portainer receives."""
import asyncio
import json
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18115
OWNER, STRANGER = 111, 222
os.environ.update({"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "key-123", "PORTAINER_ENDPOINT_ID": "3",
                   "PORTAINER_ALLOWED_USER_IDS": f"{OWNER}", "PORTAINER_PROTECTED": "jellyfin", "HOSTNAME": "abcdef123456"})

import discord
import services.portainer_service as PS
import commands.container as C
import isolate  # state files go to a temporary folder

# name: [id, image, state, status]
CONTAINERS = {
    "sonarr": ["s" * 64, "linuxserver/sonarr", "running", "Up 2 hours (healthy)"],
    "jellyfin": ["j" * 64, "jellyfin/jellyfin", "running", "Up 3 days"],
    "aegis-bot": ["abcdef123456" + "0" * 52, "aegis-bot", "running", "Up 1 hour"],
    "portainer": ["p" * 64, "portainer/portainer-ce:2.21", "running", "Up 5 days"],
    "old-app": ["o" * 64, "busybox", "exited", "Exited (1) 2 days ago"],
    "radarr": ["r" * 64, "linuxserver/radarr", "running", "Up About a minute (unhealthy)"],
    "minecraft": ["m" * 64, "itzg/minecraft-server", "exited", "Exited (143) 4 hours ago"],
    "scratch": ["c" * 64, "busybox", "created", "Created"],
}
REQUESTS = []


def frame(stream, text):
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


async def docker(request):
    path = request.match_info["path"]
    REQUESTS.append((request.method, path, dict(request.query), request.headers.get("X-API-Key")))
    if request.headers.get("X-API-Key") != "key-123":
        return web.json_response({"message": "Unauthorized"}, status=401)
    if path == "containers/json":
        return web.json_response([{"Id": i, "Names": [f"/{n}"], "Image": img, "State": st, "Status": stat}
                                  for n, (i, img, st, stat) in CONTAINERS.items()])
    by_id = {v[0]: k for k, v in CONTAINERS.items()}
    parts = path.split("/")
    name = by_id.get(parts[1]) if len(parts) > 1 else None
    if name is None:
        return web.json_response({"message": "No such container"}, status=404)
    record = CONTAINERS[name]
    action = parts[2] if len(parts) > 2 else ""
    if request.method == "GET" and action == "json":
        return web.json_response({"State": {"Status": record[2], "Running": record[2] == "running", "StartedAt": "2026-10-11T10:00:00.123Z",
                                            "FinishedAt": "0001-01-01T00:00:00Z", "ExitCode": 0},
                                  "RestartCount": 2, "Config": {"Image": record[1], "Tty": False}})
    if request.method == "GET" and action == "logs":
        return web.Response(body=frame(1, "line one\n") + frame(2, "line two\n"))
    if request.method == "POST" and action in ("start", "stop", "restart", "kill"):
        running = record[2] == "running"
        if (action == "start" and running) or (action == "stop" and not running):
            return web.Response(status=304)
        record[2], record[3] = ("exited", "Exited (137) now") if action in ("stop", "kill") else ("running", "Up 1 second")
        return web.Response(status=204)
    if request.method == "DELETE" and len(parts) == 2:
        del CONTAINERS[name]
        return web.Response(status=204)
    return web.Response(status=400)


class User:
    def __init__(s, uid): s.id = uid; s.display_name = f"user{uid}"; s.display_avatar = types.SimpleNamespace(url="https://cdn.example/a.png")
    def __str__(s): return s.display_name


class Message:
    count = 0
    def __init__(s, **k):
        Message.count += 1; s.id = Message.count; s.flags = types.SimpleNamespace(ephemeral=True); s.channel = None; s.__dict__.update(k)


class Interaction:
    """Records what the command shows: the last embed and view, private replies and modals."""
    def __init__(s, uid=OWNER, message=None):
        s.user = User(uid); s.message = message; s.shown = []; s.modal = None; s._done = False
        s.response = types.SimpleNamespace(is_done=lambda: s._done, defer=s._defer, send_message=s._send,
                                           edit_message=s._edit, send_modal=s._modal)
        s.followup = types.SimpleNamespace(send=s._send)
    async def _defer(s, **k): s._done = True
    async def _send(s, content=None, **k):
        s._done = True; s.shown.append(("send", content, k.get("embed"), k.get("view"), k.get("ephemeral"))); return Message()
    async def _edit(s, **k): s._done = True; s.shown.append(("edit", None, k.get("embed"), k.get("view"), None))
    async def edit_original_response(s, **k): s.shown.append(("edit", None, k.get("embed"), k.get("view"), None)); return Message()
    async def _modal(s, modal): s._done = True; s.modal = modal
    @property
    def embed(s): return s.shown[-1][2]
    @property
    def view(s): return s.shown[-1][3]


def labels(view):
    return [(b.label, b.disabled) for b in view.children]


def press(view, label):
    button = next(b for b in view.children if b.label == label)
    i = Interaction(view.user_id)
    return i, button.callback(i)


async def main():
    app = web.Application(); app.add_routes([web.route("*", "/api/endpoints/3/docker/{path:.*}", docker)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    cog = C.ContainerCog()
    try:
        print("== access ==")
        i = Interaction(STRANGER); await cog.list_containers.callback(cog, i)
        print("  stranger:", i.shown[0][1], "| private:", i.shown[0][4], "| requests:", len(REQUESTS))
        print("  autocomplete for stranger:", await cog._names(Interaction(STRANGER), ""))
        print("  autocomplete for owner 'jelly':", [c.name for c in await cog._names(Interaction(OWNER), "jelly")])
        os.environ["PORTAINER_API_KEY"] = ""
        i = Interaction(); await cog.list_containers.callback(cog, i)
        print("  not configured:", i.shown[0][1][:30])
        os.environ["PORTAINER_API_KEY"] = "key-123"

        print("\n== list ==")
        i = Interaction(); await cog.list_containers.callback(cog, i)
        print("  title:", i.embed.title, "| private:", i.shown[0][4], "| view message set:", i.view.message is not None)
        print("  api key header sent:", all(r[3] == "key-123" for r in REQUESTS), "| all=1:", REQUESTS[-1][2].get("all"))
        print("  is an ExpiringView:", isinstance(i.view, C.ExpiringView))
        for line in i.embed.description.split("\n"):
            print("  |", line)
        print("  color warns about problems:", getattr(i.embed.color, "value", i.embed.color) == C.theme.WARNING_COLOR)
        print("  short ages:", [C._short_age(t) for t in ("Less than a second", "About an hour", "5 weeks", "3 months", "2 years")])

        print("\n== info ==")
        i = Interaction(); await cog.info.callback(cog, i, "sonarr")
        print("  title:", i.embed.title, "| buttons:", labels(i.view))
        i = Interaction(); await cog.info.callback(cog, i, "missing")
        print("  unknown name:", i.embed.description)

        print("\n== protection ==")
        for name in ("jellyfin", "aegis-bot", "portainer"):
            i = Interaction(); await cog.stop.callback(cog, i, name)
            print(f"  stop {name}:", i.embed.description)
        stops = [r for r in REQUESTS if r[0] == "POST"]
        print("  stop requests sent:", len(stops))
        try:
            await PS.PortainerService().perform("kill", {"name": "jellyfin", "id": "j" * 64, "image": "jellyfin/jellyfin"})
        except PS.PortainerError as ex:
            print("  service refuses too:", ex)

        print("\n== stop: confirm ==")
        i = Interaction(); await cog.stop.callback(cog, i, "sonarr")
        print("  question:", i.embed.description.split("\n\n")[0], "| buttons:", labels(i.view))
        other = Interaction(STRANGER); print("  stranger presses:", await i.view.interaction_check(other), other.shown[0][1])
        b, task = press(i.view, "Stop"); await task
        print("  result:", b.embed.description.split("\n\n")[0] if b.embed.description else b.embed.fields[0])
        print("  sent:", [r[:3] for r in REQUESTS if r[0] == "POST"][-1], "| state now:", CONTAINERS["sonarr"][2])

        print("\n== stop: cancel ==")
        i = Interaction(); await cog.restart.callback(cog, i, "sonarr")
        before = len([r for r in REQUESTS if r[0] == "POST"])
        b, task = press(i.view, "Cancel"); await task
        print("  nothing sent:", len([r for r in REQUESTS if r[0] == "POST"]) == before, "| shown:", b.embed.description.split("\n")[0])

        print("\n== start ==")
        i = Interaction(); await cog.start.callback(cog, i, "sonarr")
        print("  first:", i.embed.description.split("\n")[0])
        i = Interaction(); await cog.start.callback(cog, i, "sonarr")
        print("  again:", i.embed.description.split("\n")[0])

        print("\n== kill: typed name ==")
        i = Interaction(); await cog.kill.callback(cog, i, "sonarr")
        b, task = press(i.view, "Kill"); await task
        print("  modal:", b.modal.title)
        wrong = Interaction(); b.modal.answer._value = "sonar"; await b.modal.on_submit(wrong)
        print("  wrong name:", wrong.embed.description.split("\n")[0], "| state:", CONTAINERS["sonarr"][2])
        i = Interaction(); await cog.kill.callback(cog, i, "sonarr")
        b, task = press(i.view, "Kill"); await task
        right = Interaction(); b.modal.answer._value = "SONARR "; await b.modal.on_submit(right)
        print("  right name:", right.embed.description.split("\n")[0], "| state:", CONTAINERS["sonarr"][2])

        print("\n== remove ==")
        i = Interaction(); await cog.remove.callback(cog, i, "jellyfin")
        print("  protected:", i.embed.description)
        CONTAINERS["sonarr"][2] = "running"
        i = Interaction(); await cog.remove.callback(cog, i, "sonarr")
        print("  running:", i.embed.description)
        i = Interaction(); await cog.remove.callback(cog, i, "old-app")
        print("  step 1:", "Step 1 of 3." in i.embed.description)
        b, task = press(i.view, "Continue"); await task
        typed = Interaction(); b.modal.answer._value = "old-app"; await b.modal.on_submit(typed)
        print("  step 3:", "Step 3 of 3." in typed.embed.description, "| buttons:", labels(typed.view))
        f, task = press(typed.view, "Remove permanently"); await task
        print("  result:", f.embed.title, "|", f.embed.description)
        print("  delete params:", [r[2] for r in REQUESTS if r[0] == "DELETE"], "| gone:", "old-app" not in CONTAINERS)

        print("\n== logs ==")
        i = Interaction(); await cog.logs.callback(cog, i, "sonarr", 20)
        print("  framing removed:", "line one\nline two" in i.embed.description, "| tail:", [r[2].get("tail") for r in REQUESTS if r[1].endswith("/logs")][-1])
        print("  demultiplex leaves plain text alone:", PS.demultiplex(b"plain text") == b"plain text")

        print("\n== wrong key ==")
        os.environ["PORTAINER_API_KEY"] = "bad"; PS._list_cache["data"] = None
        i = Interaction(); await cog.list_containers.callback(cog, i)
        print("  error:", i.shown[0][2].description)
    finally:
        await runner.cleanup()

asyncio.run(main())
