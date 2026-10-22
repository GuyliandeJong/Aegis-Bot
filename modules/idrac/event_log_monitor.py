"""Alerts for new entries in iDRAC's System Event Log."""

import json
import os
from datetime import datetime

import aiohttp

from config.paths import DATA_DIR
from core.base_monitor import BaseMonitor
from modules.idrac.hardware_service import HardwareService
from utils.logger import logger


def _flag(name: str, default: str) -> bool:
    value = os.getenv(name, default)
    return value.strip().lower() not in {"0", "false", "no", "off"}


class EventLogMonitor(BaseMonitor):
    """
    Reads the System Event Log (SEL) through iDRAC every five minutes.

    The first check only records the newest entry, so the history
    already in the log is not posted. Afterwards every newer entry is
    reported: Critical as critical, Warning as a warning. OK entries
    (informational, such as "log cleared" or a sensor returning to
    normal) are ignored unless IDRAC_EVENT_LOG_ALL=true; they are then
    posted as info. The new entries of one check are grouped into one
    alert per severity.

    IDRAC_EVENT_LOG=false turns it off. When iDRAC answers 404 for the
    log, that is logged once and the monitor stops until the next
    restart.
    """

    STATE_FILE = DATA_DIR / "event_log_monitor_state.json"

    INTERVAL = 300
    TIMEOUT = 180

    # Entries listed in one alert; the rest are counted.
    MAX_LINES = 10

    def __init__(self, notification_service):
        self.service = HardwareService()
        self.notification_service = notification_service
        # last_created: Created of the newest entry seen (ISO text).
        # last_ids: Ids of the entries with exactly that time, so a
        # second entry in the same second still counts as new.
        self.state = {"initialized": False, "last_created": None, "last_ids": []}
        self.unavailable = False
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[EventLogMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[EventLogMonitor] Failed to save state: %s", ex)

    @staticmethod
    def enabled() -> bool:
        return _flag("IDRAC_EVENT_LOG", "true")

    @staticmethod
    def report_all() -> bool:
        return _flag("IDRAC_EVENT_LOG_ALL", "false")

    def _last_created(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.state["last_created"])
        except (TypeError, ValueError):
            return None

    def _is_new(self, entry: dict, last: datetime | None) -> bool:
        if last is None:
            return True

        if entry["created"] != last:
            return entry["created"] > last

        return entry["id"] not in self.state["last_ids"]

    def _remember(self, entries: list[dict]) -> None:
        """Move the newest-seen mark to the newest of these entries."""
        if not entries:
            return

        newest = max(entry["created"] for entry in entries)
        last = self._last_created()
        ids = [entry["id"] for entry in entries if entry["created"] == newest]

        if last is None or newest > last:
            self.state["last_created"] = newest.isoformat()
            self.state["last_ids"] = ids
        elif newest == last:
            self.state["last_ids"] = sorted(set(self.state["last_ids"]) | set(ids))

    async def check(self):
        if self.unavailable or not self.enabled():
            return

        try:
            entries = await self.service.get_event_log()
        except aiohttp.ClientResponseError as ex:
            if ex.status != 404:
                raise

            self.unavailable = True
            logger.warning(
                "[EventLogMonitor] iDRAC has no System Event Log "
                "(404); not checking it again until the next restart."
            )
            return

        if not self.state["initialized"]:
            self._remember(entries)
            self.state["initialized"] = True
            self.save_state()
            logger.info(
                "[EventLogMonitor] First check: %s existing entries recorded.",
                len(entries),
            )
            return

        last = self._last_created()
        new = sorted(
            (entry for entry in entries if self._is_new(entry, last)),
            key=lambda entry: (entry["created"], entry["id"]),
        )

        if not new:
            return

        self._remember(new)
        self.save_state()

        for severity in ("Critical", "Warning", "OK"):
            group = [entry for entry in new if entry["severity"] == severity]

            if not group:
                continue

            logger.info(
                "[EventLogMonitor] %s new %s entries.",
                len(group),
                severity,
            )

            if severity == "OK" and not self.report_all():
                continue

            await self._report(severity, group)

    def _line(self, entry: dict) -> str:
        when = f"<t:{int(entry['created'].timestamp())}:f>"
        message = entry["message"] or "(no message)"

        if len(message) > 200:
            message = message[:199] + "…"

        if entry["sensor"]:
            return f"• {when} · **{entry['sensor']}**\n{message}"

        return f"• {when}\n{message}"

    async def _report(self, severity: str, entries: list[dict]) -> None:
        lines = [self._line(entry) for entry in entries[: self.MAX_LINES]]
        rest = len(entries) - self.MAX_LINES

        if rest > 0:
            lines.append(f"…and {rest} more in iDRAC's System Event Log.")

        description = "\n\n".join(lines)

        if len(entries) == 1:
            title = f"Hardware Event — {entries[0]['sensor'] or 'iDRAC'}"
        else:
            title = f"{len(entries)} Hardware Events"

        if severity == "Critical":
            await self.notification_service.critical(
                title=title,
                description=description,
            )
        elif severity == "Warning":
            await self.notification_service.warning(
                title=title,
                description=description,
            )
        else:
            await self.notification_service.info(
                title=title,
                description=description,
            )
