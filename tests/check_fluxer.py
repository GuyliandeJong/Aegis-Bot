"""Mirroring alerts to a Fluxer webhook: off when not set, the embed sent, Discord markup made readable, limits, and failures that never touch the Discord alert."""
import asyncio
import logging
import os
import sys
import time
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
PORT = 18185
os.environ.pop("FLUXER_WEBHOOK_URL", None)

import services.fluxer_webhook as FW
from services.notification_service import NotificationService
from utils import theme
import isolate  # state files go to a temporary folder

TOKEN = "s3cr3t-T0ken"
BASE = f"http://127.0.0.1:{PORT}/v1/webhooks/1501314428688998182"
RECEIVED = []
LOGS = []


class Capture(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            LOGS.append(record.getMessage())


logging.getLogger("aegis").addHandler(Capture())


async def execute(request):
    """Execute webhook: POST /v1/webhooks/{webhook_id}/{token}, 204 without ?wait=true."""
    mode = request.match_info["token"]
    RECEIVED.append((mode, request.headers.get("Content-Type", ""), await request.json()))
    if mode == "slow":
        await asyncio.sleep(5)
    if mode == "broken":
        return web.json_response({"code": "CONTENT_BLOCKED", "message": "blocked"}, status=403)
    if mode == "limited" and sum(1 for r in RECEIVED if r[0] == "limited") == 1:
        return web.json_response({"code": "RATE_LIMITED", "retry_after": 0.2, "global": False}, status=429)
    return web.Response(status=204)


class Channel:
    def __init__(s): s.sent = []
    async def send(s, content=None, embed=None, **k):
        s.sent.append(embed.title)
        return types.SimpleNamespace(channel=s)


class Bot:
    def __init__(s): s.channel = Channel(); s.guilds = []
    def get_channel(s, number): return s.channel if number == 10 else types.SimpleNamespace(name="alerts")
    def get_user(s, number): return types.SimpleNamespace(name="ada", display_name="Ada") if number == 222 else None


async def settle():
    await asyncio.gather(*list(FW._tasks))


def reset(token):
    os.environ["FLUXER_WEBHOOK_URL"] = f"{BASE}/{token}"
    RECEIVED.clear(); LOGS.clear()
    FW._last_log = 0.0; FW._unlogged = 0


async def main():
    app = web.Application(); app.add_routes([web.post("/v1/webhooks/{webhook_id}/{token}", execute)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        bot = Bot(); notes = NotificationService(bot)

        print("== not configured ==")
        await notes.warning(title="High CPU Usage", description="CPU at 95%.")
        await settle()
        print("  discord:", bot.channel.sent, "| fluxer posts:", len(RECEIVED), "| scheduled:", FW.start("t", "d", 0))

        print("\n== configured ==")
        reset(TOKEN)
        await notes.critical(
            title="Torrent Error",
            description="**Dune** failed <t:1792382400:f> (<t:1792382400:R>), added by <@222> in <#5> <:ok:123>.",
            content="<@222>",
            fields=[("Since", "<t:1792382400:D> <t:1792382400:t>", True), ("Who", "<@999>", True), ("", "", False)],
        )
        await settle()
        mode, kind, body = RECEIVED[0]
        embed = body["embeds"][0]
        print("  content type:", kind.split(";")[0], "| keys:", sorted(body), "| embed keys:", sorted(embed))
        print("  title:", embed["title"], "| color:", hex(embed["color"]), "== ERROR_COLOR:", embed["color"] == theme.ERROR_COLOR)
        print("  description:", embed["description"])
        print("  fields:", [(f["name"], f["value"], f["inline"]) for f in embed["fields"]])
        print("  footer:", embed["footer"], "| timestamp ISO:", embed["timestamp"].endswith("+00:00"))
        print("  no Discord markup left:", "<t:" not in str(body) and "<@" not in str(body) and "<#" not in str(body))

        print("\n== every level ==")
        reset(TOKEN)
        for level in ("info", "warning", "critical", "recovered", "success"):
            await getattr(notes, level)(title=level.title(), description="x")
        await settle()
        print("  ", [(b["embeds"][0]["title"], hex(b["embeds"][0]["color"])) for _, _, b in RECEIVED])

        print("\n== weekly summary ==")
        reset(TOKEN)
        await notes.info(
            title="Weekly summary",
            description="Since <t:1791777600:D>.",
            fields=[("🗄️ Backup", "<t:1792382400:f>: 1.20 GiB, Proton Drive.", False), ("🔔 Alerts", "None.", False)],
        )
        await settle()
        embed = RECEIVED[0][2]["embeds"][0]
        print("  summary:", embed["description"], "|", embed["fields"][0]["value"])

        print("\n== limits ==")
        reset(TOKEN)
        await notes.info(title="T" * 300, description="D" * 5000, fields=[(f"n{i}", "v" * 2000, True) for i in range(30)])
        await settle()
        embed = RECEIVED[0][2]["embeds"][0]
        print("  title", len(embed["title"]), "| description", len(embed["description"]), "| fields", len(embed["fields"]),
              "| value", max(len(f["value"]) for f in embed["fields"]))

        print("\n== slow Fluxer does not delay Discord ==")
        reset("slow"); FW.TIMEOUT = 1
        started = time.monotonic()
        await notes.warning(title="Slow One", description="x")
        print("  discord sent in under 0.5 s:", time.monotonic() - started < 0.5, "| last discord:", bot.channel.sent[-1])
        await settle()
        print("  logged:", LOGS)
        FW.TIMEOUT = 10

        print("\n== refused, logging limited ==")
        reset("broken")
        for i in range(3):
            await notes.warning(title=f"Refused {i}", description="x")
        await settle()
        print("  discord:", bot.channel.sent[-3:], "| warnings logged:", len(LOGS), "| unlogged:", FW._unlogged)
        print("  logged:", LOGS[0])
        FW._last_log -= FW.LOG_INTERVAL
        await notes.warning(title="Refused 3", description="x"); await settle()
        print("  after the interval:", LOGS[-1])

        print("\n== rate limited once ==")
        reset("limited")
        await notes.info(title="After 429", description="x"); await settle()
        print("  attempts:", len(RECEIVED), "| logged:", LOGS)

        print("\n== not reachable ==")
        os.environ["FLUXER_WEBHOOK_URL"] = f"http://127.0.0.1:{PORT + 1}/v1/webhooks/1/{TOKEN}"
        LOGS.clear(); FW._last_log = 0.0
        await notes.critical(title="Nobody Home", description="x"); await settle()
        print("  discord:", bot.channel.sent[-1], "| logged once:", len(LOGS) == 1, "| token hidden:", TOKEN not in " ".join(LOGS))
    finally:
        await runner.cleanup()

asyncio.run(main())
