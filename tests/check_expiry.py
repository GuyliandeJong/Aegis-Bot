"""View expiry through discord.py's real Interaction/InteractionMessage against a fake Discord API."""
import asyncio, json, logging, os, sys, types
import aiohttp
from aiohttp import web
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18099
import discord
from discord.webhook import async_ as webhook_async
import modules.torrents.torrent.search as SM
import isolate  # state files go to a temporary folder
webhook_async.Route.BASE = f"http://127.0.0.1:{PORT}/api"
CALLS = []
USER = {"id": "7", "username": "tester", "discriminator": "0", "avatar": None, "global_name": "Tester"}
BOT = {"id": "2", "username": "Aegis-Bot", "discriminator": "2867", "avatar": None, "bot": True}
def message(components=None, content=""):
    return {"id": "5", "channel_id": "10", "author": BOT, "content": content, "timestamp": "2026-10-07T14:20:16+00:00", "edited_timestamp": None,
            "tts": False, "mention_everyone": False, "mentions": [], "mention_roles": [], "attachments": [], "embeds": [], "pinned": False,
            "type": 0, "components": components or [], "flags": 0}
def J(data): return web.Response(body=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
async def edit_original(request):
    body = await request.json()
    comps = body.get("components") or []
    CALLS.append({"content": body.get("content"), "disabled": [c.get("disabled") for row in comps for c in row["components"]]})
    return J(message(comps, body.get("content") or ""))
async def main():
    app = web.Application(); app.add_routes([web.patch("/api/webhooks/{app}/{token}/messages/@original", edit_original)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    client = discord.Client(intents=discord.Intents.default()); session = aiohttp.ClientSession(); client.http._HTTPClient__session = session
    payload = {"id": "1", "application_id": "99", "type": 2, "token": "tok", "version": 1, "channel_id": "10",
               "channel": {"id": "10", "type": 1, "recipients": [USER]}, "user": USER, "data": {"id": "3", "name": "torrent", "type": 1},
               "entitlements": [], "authorizing_integration_owners": {}, "context": 1, "attachment_size_limit": 10485760, "locale": "en-US"}
    interaction = discord.Interaction(data=payload, state=client._connection)
    view = SM.TorrentJellyfinSearchView(7, "x", "Movies", object()); view.timeout = 0.4
    try:
        view.message = await interaction.edit_original_response(content=None, embed=discord.Embed(title="Available on X"), view=view)
        print("sent; message type:", type(view.message).__name__, "| view stored/listening:", not view.is_finished())
        await asyncio.sleep(1.5)
        print("view finished:", view.is_finished())
        for c in CALLS: print("discord <-", c)
    finally:
        await session.close(); await runner.cleanup()
asyncio.run(main())
