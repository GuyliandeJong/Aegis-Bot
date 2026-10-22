"""Once a week, suggest removing torrents that are seeded enough."""

import json
import os
from datetime import datetime
from pathlib import Path

from commands.torrent.cleanup import (
    SeedCleanupView,
    seeded_enough,
    seeding_targets,
)
from monitors.base_monitor import BaseMonitor
from monitors.weekly_summary_monitor import DAYS
from services.qbittorrent_service import QBittorrentService
from utils.formatting import format_bytes, format_compact_duration
from utils.logger import logger


class SeedCleanupMonitor(BaseMonitor):
    """
    Posts the torrents that reached a seeding target (the ones
    `/torrent cleanup` marks ✅) in the alert channel, with buttons to
    remove them all from qBittorrent, keeping their files, or to dismiss
    the suggestion. Nothing is posted when no torrent qualifies.

    .env:
        CLEANUP_REMINDER       true (default) or false
        CLEANUP_REMINDER_DAY   monday..sunday (default saturday)
        CLEANUP_REMINDER_HOUR  0-23 (default 11, container time)
        SEED_RATIO_TARGET, SEED_DAYS_TARGET as for /torrent cleanup
    """

    STATE_FILE = Path("/app/data/seed_cleanup_state.json")

    INTERVAL = 600

    # An embed description may hold 4096 characters; leave room for
    # the "…and N more" line.
    DESCRIPTION_LIMIT = 3900

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"last_run": None}
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[SeedCleanup] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[SeedCleanup] Failed to save state: %s", ex)

    @staticmethod
    def enabled() -> bool:
        value = os.getenv("CLEANUP_REMINDER", "true")
        return value.strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def schedule() -> tuple[int, int]:
        """(weekday 0-6, hour) the suggestion is due."""
        day = os.getenv("CLEANUP_REMINDER_DAY", "saturday").strip().lower()
        weekday = DAYS.index(day) if day in DAYS else 5

        try:
            hour = min(23, max(0, int(os.getenv("CLEANUP_REMINDER_HOUR", "11"))))
        except ValueError:
            hour = 11

        return weekday, hour

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    async def check(self):
        if not self.enabled():
            return

        now = self._now()
        weekday, hour = self.schedule()
        today = now.strftime("%Y-%m-%d")

        if (
            now.weekday() != weekday
            or now.hour < hour
            or self.state.get("last_run") == today
        ):
            return

        service = QBittorrentService()

        try:
            torrents = await service.get_torrents()
        except Exception as ex:
            # Not marked as done: the next check, ten minutes on, tries again.
            logger.warning("[SeedCleanup] Torrent list unavailable: %s", ex)
            return
        finally:
            await service.close()

        done = [t for t in seeded_enough(torrents) if t.get("hash")]

        self.state["last_run"] = today
        self.save_state()

        if not done:
            logger.info("[SeedCleanup] No torrents reached the seeding target.")
            return

        ratio_target, days_target = seeding_targets()
        size = sum(int(t.get("size") or 0) for t in done)
        count = f"{len(done)} torrent{'s' if len(done) != 1 else ''}"

        view = SeedCleanupView(done)

        message = await self.notification_service.info(
            title="Torrents seeded enough",
            description=self.describe(
                f"{count} reached ratio {ratio_target:g} or "
                f"{days_target:g} days of seeding, {format_bytes(size)} "
                "in total. Removing them from qBittorrent keeps the "
                "files on disk.",
                done,
            ),
            view=view,
            # A suggestion, not an alert: posted during maintenance too,
            # since this week's run is already marked as done.
            exempt=True,
        )

        if message is None:
            view.stop()
            return

        view.message = message

        logger.info("[SeedCleanup] Suggested removing %s.", count)

    @classmethod
    def describe(cls, summary: str, torrents: list[dict]) -> str:
        """The summary and one line per torrent, as many as fit."""
        text = summary + "\n"

        for index, torrent in enumerate(torrents):
            line = (
                f"\n• **{str(torrent.get('name'))[:60]}** · ratio "
                f"{float(torrent.get('ratio') or 0):.2f} · seeded "
                f"{format_compact_duration(torrent.get('seeding_time'))} · "
                f"{format_bytes(torrent.get('size'))}"
            )

            if len(text) + len(line) > cls.DESCRIPTION_LIMIT:
                text += (
                    f"\n…and {len(torrents) - index} more; "
                    "`/torrent cleanup` lists them all."
                )
                break

            text += line

        return text
