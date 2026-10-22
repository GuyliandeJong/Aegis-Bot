"""Drive the series flow through discord.py's real Interaction + HTTP code against a fake Discord API."""
import asyncio
import json
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18097
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h",
                   "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "k", "JELLYFIN_2_NAME": "jellyfin.example",
                   "JELLYFIN_SERIES_DIR": str(Path(tempfile.gettempdir()) / "aegis-real-series")})

import discord
from discord.webhook import async_ as webhook_async
import discord.http
import modules.jellyfin.jellyfin_download as JD
import isolate  # state files go to a temporary folder

webhook_async.Route.BASE = f"http://127.0.0.1:{PORT}/api"
DISCORD_CALLS = []
USER = {"id": "7", "username": "tester", "discriminator": "0", "avatar": None, "global_name": "Tester"}
BOT = {"id": "2", "username": "Aegis-Bot", "discriminator": "2867", "avatar": None, "bot": True}


def message(components=None):
    return {"id": "5", "channel_id": "10", "author": BOT, "content": "", "timestamp": "2026-10-07T14:20:16+00:00", "edited_timestamp": None,
            "tts": False, "mention_everyone": False, "mentions": [], "mention_roles": [], "attachments": [], "embeds": [], "pinned": False,
            "type": 0, "components": components or [], "flags": 0}


def J(data, headers=None):
    h = {"Content-Type": "application/json"}; h.update(headers or {})
    return web.Response(body=json.dumps(data).encode(), headers=h)


async def callback(request):
    body = await request.json()
    DISCORD_CALLS.append(("callback", body["type"], [e.get("title") for e in body["data"].get("embeds", [])], len(body["data"].get("components", []))))
    return J({"interaction": {"id": "1", "type": 3, "response_message_id": "5", "response_message_loading": False,
                                              "response_message_ephemeral": False}, "resource": {"type": 7, "message": message()}})


async def edit_original(request):
    body = await request.json()
    DISCORD_CALLS.append(("edit_original", [e.get("title") for e in body.get("embeds", [])], body.get("components")))
    return J(message(body.get("components")), headers={"X-RateLimit-Remaining": "4", "X-RateLimit-Reset-After": "1.0"})


async def seasons(request):
    return web.json_response({"Items": [{"Id": "s1", "Name": "Season 1", "IndexNumber": 1}, {"Id": "s2", "Name": "Season 2", "IndexNumber": 2}]})


async def main():
    app = web.Application()
    app.add_routes([web.post("/api/interactions/{id}/{token}/callback", callback),
                    web.patch("/api/webhooks/{app}/{token}/messages/@original", edit_original),
                    web.get("/friend/Shows/{id}/Seasons", seasons)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()

    client = discord.Client(intents=discord.Intents.default())
    session = aiohttp.ClientSession()
    client.http._HTTPClient__session = session
    state = client._connection
    payload = {"id": "1", "application_id": "99", "type": 3, "token": "tok", "version": 1, "channel_id": "10",
               "channel": {"id": "10", "type": 1, "recipients": [USER]}, "user": USER, "message": message(),
               "data": {"custom_id": "x", "component_type": 2}, "entitlements": [], "authorizing_integration_owners": {},
               "context": 1, "attachment_size_limit": 10485760, "locale": "en-US"}
    interaction = discord.Interaction(data=payload, state=state)
    match = {"server": "jellyfin.example", "primary": False, "title": "Gravity Falls", "year": 2012, "item_id": "a" * 32, "path": "/tv/GF"}
    view = JD.JellyfinSourceView(7, "Gravity Falls", "Series", object(), match)
    try:
        task = asyncio.create_task(view._scheduled_task(view.download_button, interaction))
        done, pending = await asyncio.wait({task}, timeout=20)
        print("callback task:", "FINISHED" if done else "STILL RUNNING AFTER 20s (hang reproduced)")
        if pending:
            task.print_stack()
            task.cancel()
        elif task.exception():
            print("raised:", repr(task.exception()))
    finally:
        for c in DISCORD_CALLS: print("discord <-", json.dumps(c)[:230])
        await session.close(); await runner.cleanup()

asyncio.run(main())
