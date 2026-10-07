"""Writes a timestamp every minute for scripts/watchdog.sh."""

import math
import time
from pathlib import Path

from utils.logger import logger


class HeartbeatMonitor:
    """
    Touches data/heartbeat while the bot is connected to Discord.

    The watchdog script on the server reads this file. It stops being
    updated when the container stops, when the bot hangs, and when the
    bot has lost its Discord connection, which is exactly when an alert
    from inside the bot could not be sent.
    """

    FILE = Path("/app/data/heartbeat")

    INTERVAL = 60

    def __init__(self, notification_service):
        self.bot = notification_service.bot

    def connected(self) -> bool:
        latency = getattr(self.bot, "latency", float("inf"))
        return not self.bot.is_closed() and math.isfinite(latency)

    async def check(self):
        if not self.connected():
            logger.warning(
                "[Heartbeat] Not connected to Discord; heartbeat not written."
            )
            return

        self.FILE.parent.mkdir(parents=True, exist_ok=True)
        self.FILE.write_text(str(int(time.time())))
