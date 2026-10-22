"""
Once a day, fetch missing subtitles for movies already in the primary
Jellyfin library, a few at a time.
"""

import json
import os
from datetime import datetime

from config.paths import DATA_DIR
from core.base_monitor import BaseMonitor
from modules.jellyfin import subtitle_fetch
from modules.jellyfin.jellyfin_service import JellyfinService
from utils.logger import logger


class SubtitleMonitor(BaseMonitor):
    """
    Runs modules.jellyfin.subtitle_fetch.backfill once a day.

    OpenSubtitles limits downloads per day, so only
    SUBTITLE_DAILY_LIMIT movies are tried per run, newest additions
    first. New downloads get their subtitles right away through the
    library follow-up and do not wait for this run. Results go to the
    weekly summary, not to a daily message.

    .env:
        SUBTITLE_AUTO_DOWNLOAD  true (default) or false
        SUBTITLE_DAILY_LIMIT    movies per run (default 10, 0 = off)
        SUBTITLE_HOUR           hour of the day to run, 0-23
                                (default 6, container time; not 4,
                                when the Sunday backup stops Jellyfin)
    """

    STATE_FILE = DATA_DIR / "subtitle_monitor_state.json"

    # Checks every ten minutes whether today's run is due.
    INTERVAL = 600

    # Each movie can take a few minutes: a provider search, the
    # download and waiting for the file to show up.
    TIMEOUT = 3600

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"last_run": None}
        self.load_state()

    @staticmethod
    def run_hour() -> int:
        try:
            return min(23, max(0, int(os.getenv("SUBTITLE_HOUR", "6"))))
        except ValueError:
            return 6

    def load_state(self):
        try:
            data = json.loads(self.STATE_FILE.read_text())

            if isinstance(data, dict):
                self.state.update(data)
        except FileNotFoundError:
            pass
        except Exception as ex:
            logger.error("[SubtitleMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[SubtitleMonitor] Failed to save state: %s", ex)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    async def check(self):
        limit = subtitle_fetch.daily_limit()

        if not subtitle_fetch.enabled() or limit <= 0:
            return

        now = self._now()
        today = now.strftime("%Y-%m-%d")

        if self.state.get("last_run") == today or now.hour < self.run_hour():
            return

        service = JellyfinService()

        if service.primary_server() is None:
            return

        # Marked before running, so a run cut off by the timeout is not
        # started again the same day.
        self.state["last_run"] = today
        self.save_state()

        try:
            counts = await subtitle_fetch.backfill(service, limit)
        finally:
            await service.close()

        logger.info(
            "[SubtitleMonitor] %d movies without subtitles; added %d, "
            "none found %d, refused %d, failed %d.",
            counts["missing"],
            counts["added"],
            counts["none"],
            counts["refused"],
            counts["error"],
        )
