"""Alert when the backup has not succeeded for too long."""

import json
import os
import time
from pathlib import Path

from monitors.base_monitor import BaseMonitor
from utils.logger import logger


class BackupMonitor(BaseMonitor):
    """
    scripts/backup.sh reports its own failures through the watchdog
    webhook, but a backup that never runs (a broken cron job, a server
    that was off at 04:00) reports nothing. This reads the status file
    the script writes and alerts once when the last successful backup
    is older than BACKUP_MAX_AGE_DAYS, and once when one succeeds again.

    .env:
        BACKUP_MAX_AGE_DAYS  days without a successful backup before
                             the alert (default 8; 0 turns it off)
    Does nothing while BACKUP_PATHS is not set (no backup configured).
    """

    STATUS_FILE = Path("/app/data/backup_status.json")
    STATE_FILE = Path("/app/data/backup_monitor_state.json")

    INTERVAL = 3600
    TIMEOUT = 30

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"last_ok": None, "alerted": False}
        self.load_state()

    @staticmethod
    def max_age_days() -> float:
        try:
            return max(0.0, float(os.getenv("BACKUP_MAX_AGE_DAYS", "8")))
        except ValueError:
            return 8.0

    def load_state(self):
        try:
            data = json.loads(self.STATE_FILE.read_text())

            if isinstance(data, dict):
                self.state.update(data)
        except FileNotFoundError:
            pass
        except Exception as ex:
            logger.error("[BackupMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[BackupMonitor] Failed to save state: %s", ex)

    @staticmethod
    def _now() -> float:
        return time.time()

    def _read_status(self) -> dict | None:
        try:
            data = json.loads(self.STATUS_FILE.read_text())
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except Exception as ex:
            logger.warning("[BackupMonitor] Status file unreadable: %s", ex)
            return None

    async def check(self):
        days = self.max_age_days()

        if not os.getenv("BACKUP_PATHS", "").strip() or days <= 0:
            return

        status = self._read_status()
        now = self._now()

        # The status file only holds the last run; the last success is
        # remembered here, so a failed run does not hide how long ago
        # the last good one was.
        if status and status.get("ok"):
            when = float(status.get("time") or 0)

            if when > float(self.state.get("last_ok") or 0):
                self.state["last_ok"] = when

        last_ok = self.state.get("last_ok")

        if last_ok is None:
            # Never seen a success: count from the first check, so a
            # fresh setup gets its first backup's time to run.
            self.state["last_ok"] = last_ok = now
            self.save_state()

        age_days = (now - float(last_ok)) / 86400

        if age_days >= days and not self.state["alerted"]:
            self.state["alerted"] = True
            failed = status is not None and not status.get("ok")
            reason = (
                f"\n\nThe last run, <t:{int(status.get('time') or 0)}:f>, failed: "
                f"{status.get('error') or 'unknown error'}"
                if failed
                else "\n\nNo run has been recorded since. Check root's cron job "
                     "(`sudo crontab -l`) and run `sudo bash scripts/backup.sh` by hand."
            )
            await self.notification_service.warning(
                title="No Recent Backup",
                description=(
                    f"The last successful backup was <t:{int(last_ok)}:R> "
                    f"(<t:{int(last_ok)}:f>), more than {days:g} days ago.{reason}"
                ),
            )
        elif age_days < days and self.state["alerted"]:
            self.state["alerted"] = False
            await self.notification_service.recovered(
                title="Backup Succeeded Again",
                description=f"A backup succeeded <t:{int(last_ok)}:R>.",
            )

        self.save_state()
