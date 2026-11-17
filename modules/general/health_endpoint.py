"""An HTTP health endpoint for an external uptime monitor."""

import math
import os

from aiohttp import web

from core import monitor_manager
from utils.logger import logger


class HealthEndpoint:
    """
    Answers GET /health with JSON, so a tool such as Uptime Kuma can
    check the bot from outside the container.

    200 {"status": "ok"} while the bot is connected to Discord and every
    monitor reports; 503 with "degraded" when a monitor is failing or
    silent, and "down" when the Discord connection is lost. Only counts
    are shown, never names or settings.

    It starts with the first check, and is tried again every
    INTERVAL seconds when the port could not be bound.

    .env:
        HEALTH_PORT  port to listen on (empty or 0: off)
        HEALTH_HOST  address to listen on (default 0.0.0.0, inside the
                     container; publish the port in docker-compose)
    """

    INTERVAL = 30

    def __init__(self, notification_service):
        self.bot = notification_service.bot
        self.runner: web.AppRunner | None = None

    @staticmethod
    def port() -> int:
        try:
            return max(0, int(os.getenv("HEALTH_PORT", "0") or 0))
        except ValueError:
            return 0

    @staticmethod
    def host() -> str:
        return os.getenv("HEALTH_HOST", "0.0.0.0").strip() or "0.0.0.0"

    def connected(self) -> bool:
        latency = getattr(self.bot, "latency", float("inf"))
        return not self.bot.is_closed() and math.isfinite(latency)

    def answer(self) -> tuple[int, dict]:
        """(HTTP status, body)."""
        connected = self.connected()
        seen, found = monitor_manager.problems()
        body = {
            "status": "ok",
            "discord": connected,
            "monitors": seen,
            "monitors_failing": len(found),
        }

        if not connected:
            body["status"] = "down"
            return 503, body

        if found:
            body["status"] = "degraded"
            return 503, body

        return 200, body

    async def handle(self, request: web.Request) -> web.Response:
        status, body = self.answer()
        return web.json_response(body, status=status)

    async def check(self):
        port = self.port()

        if not port or self.runner is not None:
            return

        app = web.Application()
        app.router.add_get("/health", self.handle)
        runner = web.AppRunner(app)
        await runner.setup()

        try:
            await web.TCPSite(runner, self.host(), port).start()
        except OSError as ex:
            logger.error("[HealthEndpoint] Cannot listen on port %s: %s", port, ex)
            await runner.cleanup()
            return

        self.runner = runner
        logger.info("[HealthEndpoint] Listening on %s:%s.", self.host(), port)
