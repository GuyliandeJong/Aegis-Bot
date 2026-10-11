"""Once a week, one message about what happened."""

import json
import os
from datetime import datetime
from pathlib import Path

from monitors.base_monitor import BaseMonitor
from services import activity_log
from services.filesystem_service import FilesystemService
from services.qbittorrent_service import QBittorrentService
from services.release_name import parse
from utils.formatting import format_bytes, format_compact_duration
from utils.logger import logger


DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

WEEK = 7 * 24 * 60 * 60


def readable_title(name) -> str:
    """
    "Inception.2010.1080p.BluRay.x265" becomes "Inception (2010)",
    "Fallout.S02E03.1080p" becomes "Fallout — S02E03".
    """
    release = parse(name)
    title = release["title"] or str(name or "Unknown")

    if release["year"]:
        title += f" ({release['year']})"

    if release["season"] is not None:
        if release["episode"] is not None:
            title += f" — S{release['season']:02d}E{release['episode']:02d}"
        else:
            title += f" — Season {release['season']}"

    return title[:90]


class WeeklySummaryMonitor(BaseMonitor):
    """
    Posts a weekly summary in the alert channel: disk growth, torrents
    completed, titles copied from other Jellyfin servers, and alerts.

    Disk usage is compared with the snapshot taken at the previous
    summary, so the first summary only records it.

    .env:
        WEEKLY_SUMMARY       true (default) or false
        WEEKLY_SUMMARY_DAY   monday..sunday (default sunday)
        WEEKLY_SUMMARY_HOUR  0-23 (default 10, container time)
    """

    STATE_FILE = Path("/app/data/weekly_summary_state.json")

    INTERVAL = 600

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"last_run": None, "disks": {}, "since": None}
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[WeeklySummary] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[WeeklySummary] Failed to save state: %s", ex)

    @staticmethod
    def enabled() -> bool:
        value = os.getenv("WEEKLY_SUMMARY", "true")
        return value.strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def schedule() -> tuple[int, int]:
        """(weekday 0-6, hour) the summary is due."""
        day = os.getenv("WEEKLY_SUMMARY_DAY", "sunday").strip().lower()
        weekday = DAYS.index(day) if day in DAYS else 6

        try:
            hour = min(23, max(0, int(os.getenv("WEEKLY_SUMMARY_HOUR", "10"))))
        except ValueError:
            hour = 10

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

        since = self.state.get("since") or (now.timestamp() - WEEK)

        fields = await self.build(since)

        self.state["last_run"] = today
        self.state["since"] = now.timestamp()
        self.save_state()

        message = await self.notification_service.info(
            title="Weekly summary",
            description=(
                f"Since <t:{int(since)}:D>."
            ),
            fields=fields,
        )

        if self.overflow and message is not None:
            await self._post_full_lists(message, now)

    async def build(self, since: float) -> list[tuple[str, str, bool]]:
        # Lists too long for their field, posted in a thread afterwards.
        self.overflow: list[tuple[str, list[str]]] = []

        fields = [
            ("💾 Disks", await self._disks(), False),
            ("✅ Torrents completed", await self._torrents(since), False),
            ("📥 Copied from Jellyfin", self._copies(since), False),
            ("🌐 Internet", self._outages(since), False),
            ("🗄️ Backup", self._backup(since), False),
            ("🔔 Alerts", self._alerts(since), False),
        ]

        # Discord refuses a field value over 1024 characters.
        return [
            (name, value[:1024], inline)
            for name, value, inline in fields
        ]

    async def _disks(self) -> str:
        try:
            current = await FilesystemService().get_used_bytes()
        except Exception as ex:
            logger.warning("[WeeklySummary] Disk usage unavailable: %s", ex)
            return "Not available."

        previous = self.state.get("disks") or {}
        self.state["disks"] = {
            mount: used for mount, (used, _total) in current.items()
        }

        lines = []

        for mount, (used, total) in sorted(current.items()):
            line = (
                f"`{mount}` {format_bytes(used)} of {format_bytes(total)} "
                f"({used / total * 100:.0f}%)"
                if total
                else f"`{mount}` {format_bytes(used)}"
            )

            if mount in previous:
                change = used - int(previous[mount])
                sign = "+" if change >= 0 else "−"
                line += f", {sign}{format_bytes(abs(change))} this week"

            lines.append(line)

        if not previous:
            lines.append("Growth is shown from next week on.")

        return "\n".join(lines) or "No filesystems found."

    async def _torrents(self, since: float) -> str:
        service = QBittorrentService()

        try:
            torrents = await service.get_torrents()
        except Exception as ex:
            logger.warning("[WeeklySummary] Torrent list unavailable: %s", ex)
            return "qBittorrent did not answer."
        finally:
            await service.close()

        done = [
            torrent
            for torrent in torrents
            if int(torrent.get("completion_on") or 0) >= since
        ]

        if not done:
            return "None."

        done.sort(key=lambda t: int(t.get("completion_on") or 0))
        size = sum(int(torrent.get("size") or 0) for torrent in done)

        lines = [
            f"• {readable_title(t.get('name'))} · "
            f"{format_bytes(t.get('size'))}"
            for t in done
        ]

        return self._list_field(
            "✅ Torrents completed",
            f"{len(done)} torrent{'s' if len(done) != 1 else ''}, "
            f"{format_bytes(size)}",
            lines,
        )

    def _copies(self, since: float) -> str:
        copies = activity_log.since(since, "jellyfin_copy")

        if not copies:
            return "None."

        files = sum(int(c.get("files") or 0) for c in copies)
        size = sum(int(c.get("bytes") or 0) for c in copies)

        lines = [
            f"• {c.get('title')} · {int(c.get('files') or 0)} file(s), "
            f"{format_bytes(c.get('bytes'))}"
            for c in copies
        ]

        return self._list_field(
            "📥 Copied from Jellyfin",
            f"{len(copies)} request(s), {files} file(s), "
            f"{format_bytes(size)}",
            lines,
        )

    # A field value may hold 1024 characters.
    FIELD_LIMIT = 1024

    def _list_field(self, heading: str, total: str, lines: list[str]) -> str:
        """
        The whole list when it fits in the field; otherwise the total,
        with the list kept for a thread under the summary.
        """
        text = total + "\n" + "\n".join(lines)

        if len(text) <= self.FIELD_LIMIT:
            return text

        self.overflow.append((heading, lines))

        return f"{total}\nThe full list is in the thread below."

    # A message may hold 2000 characters.
    MESSAGE_LIMIT = 1900

    async def _post_full_lists(self, message, now: datetime) -> None:
        """
        Post the lists that did not fit in a thread under the summary,
        or below it when the bot may not create threads.
        """
        target = message.channel

        try:
            target = await message.create_thread(
                name=f"Weekly summary {now.strftime('%Y-%m-%d')}",
                # Seven days, then Discord archives it; it stays readable.
                auto_archive_duration=10080,
            )
        except Exception as ex:
            logger.warning(
                "[WeeklySummary] Could not create a thread, posting the "
                "lists in the channel: %s",
                ex,
            )

        for heading, lines in self.overflow:
            chunk = f"**{heading}**"

            for line in lines:
                if len(chunk) + len(line) + 1 > self.MESSAGE_LIMIT:
                    await target.send(chunk)
                    chunk = ""

                chunk = f"{chunk}\n{line}" if chunk else line

            if chunk:
                await target.send(chunk)

    # Written by scripts/backup.sh, which runs on the server from cron.
    BACKUP_STATUS = Path("/app/data/backup_status.json")

    @classmethod
    def _backup(cls, since: float) -> str:
        try:
            status = json.loads(cls.BACKUP_STATUS.read_text())
        except FileNotFoundError:
            return "No backup has run yet."
        except Exception as ex:
            logger.warning("[WeeklySummary] Backup status unreadable: %s", ex)
            return "Backup status unreadable."

        when = int(status.get("time") or 0)

        if when < since:
            return f"No backup this week; the last one ran <t:{when}:D>."

        if not status.get("ok"):
            return f"Failed <t:{when}:f>: {status.get('error') or 'unknown error'}"

        where = (
            f"uploaded to {status.get('remote')}"
            if status.get("uploaded")
            else "kept locally only"
        )

        line = f"<t:{when}:f>: {format_bytes(status.get('size'))}, {where}."

        mirrored = int(status.get("mirrored") or 0)

        if mirrored:
            changed = int(status.get("changed") or 0)
            line += (
                f" Mirrored {mirrored} folder{'s' if mirrored != 1 else ''}, "
                f"{changed} file{'s' if changed != 1 else ''} "
                f"{'uploaded' if status.get('uploaded') else 'changed'}."
            )

        return line

    @staticmethod
    def _outages(since: float) -> str:
        outages = activity_log.since(since, "wan_outage")

        if not outages:
            return "No outages."

        durations = [int(o.get("seconds") or 0) for o in outages]

        return (
            f"{len(outages)} outage{'s' if len(outages) != 1 else ''}, "
            f"{format_compact_duration(sum(durations))} in total, "
            f"longest {format_compact_duration(max(durations))}."
        )

    @staticmethod
    def _alerts(since: float) -> str:
        alerts = activity_log.since(since, "alert")
        counts = {}

        for alert in alerts:
            level = alert.get("level") or "info"
            counts[level] = counts.get(level, 0) + 1

        problems = counts.get("critical", 0) + counts.get("warning", 0)

        if not problems:
            return "No warnings or critical alerts."

        parts = [
            f"{counts[level]} {level}"
            for level in ("critical", "warning", "recovered")
            if counts.get(level)
        ]

        titles = [
            a.get("title")
            for a in alerts
            if a.get("level") in {"critical", "warning"}
        ]
        recent = ", ".join(str(t)[:40] for t in titles[-5:])

        return f"{', '.join(parts)}. Latest: {recent}"
