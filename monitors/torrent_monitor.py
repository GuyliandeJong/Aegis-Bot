import json
from pathlib import Path

from config.settings import settings
from monitors.base_monitor import BaseMonitor
from services.qbittorrent_service import QBittorrentService
from utils.logger import logger


class TorrentMonitor(BaseMonitor):
    """Detect completed torrents and retain completion statistics."""

    STATE_FILE = Path(
        "/app/data/torrent_monitor_state.json"
    )

    # Torrent monitoring needs finer sampling than the general
    # 60-second system-monitor interval so speed peaks can be captured.
    INTERVAL = 10

    COMPLETED_STATES = {
        "completed",
    }

    def __init__(
        self,
        notification_service,
    ):
        self.service = QBittorrentService()
        self.notification_service = notification_service
        self.state = {}

        self.load_state()

    def load_state(self):
        """Load persistent torrent monitoring state."""

        if not self.STATE_FILE.exists():
            return

        try:
            with self.STATE_FILE.open("r") as file:
                data = json.load(file)

            if isinstance(data, dict):
                self.state = data

                logger.info(
                    "[TorrentMonitor] Loaded state for %d torrents.",
                    len(self.state),
                )

            else:
                logger.warning(
                    "[TorrentMonitor] Invalid state file format."
                )

        except Exception as ex:
            logger.error(
                "[TorrentMonitor] Failed to load state: %s",
                ex,
            )

    def save_state(self):
        """Persist torrent monitoring state atomically."""

        temp_file = self.STATE_FILE.with_suffix(
            ".tmp"
        )

        try:
            self.STATE_FILE.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with temp_file.open("w") as file:
                json.dump(
                    self.state,
                    file,
                    indent=4,
                )

                file.flush()

            temp_file.replace(
                self.STATE_FILE
            )

        except Exception as ex:
            logger.error(
                "[TorrentMonitor] Failed to save state: %s",
                ex,
            )

            try:
                temp_file.unlink(
                    missing_ok=True
                )
            except OSError:
                pass

    @staticmethod
    def number(
        value,
        default=0.0,
    ):
        """Safely convert a value to float."""

        try:
            return float(value)

        except (
            TypeError,
            ValueError,
        ):
            return default

    @staticmethod
    def integer(
        value,
        default=0,
    ):
        """Safely convert a value to integer."""

        try:
            return int(value)

        except (
            TypeError,
            ValueError,
        ):
            return default

    @classmethod
    def is_completed(
        cls,
        torrent,
    ):
        """Determine whether a torrent has completed."""

        state = str(
            torrent.get(
                "state",
                "",
            )
        ).lower()

        progress = cls.number(
            torrent.get(
                "progress",
                0,
            )
        )

        return (
            state in cls.COMPLETED_STATES
            or progress >= 1.0
        )

    @classmethod
    def format_bytes(
        cls,
        value,
    ):
        """Format bytes as a human-readable binary size."""

        value = max(
            0.0,
            cls.number(value),
        )

        units = (
            "B",
            "KiB",
            "MiB",
            "GiB",
            "TiB",
            "PiB",
        )

        for unit in units:
            if (
                value < 1024
                or unit == units[-1]
            ):
                if unit == "B":
                    return f"{value:.0f} {unit}"

                if value >= 100:
                    return f"{value:.0f} {unit}"

                if value >= 10:
                    return f"{value:.1f} {unit}"

                return f"{value:.2f} {unit}"

            value /= 1024

        return "0 B"

    @classmethod
    def format_speed(
        cls,
        value,
    ):
        """Format bytes per second."""

        return (
            f"{cls.format_bytes(value)}/s"
        )

    @classmethod
    def format_duration(
        cls,
        value,
    ):
        """Format seconds as a compact human-readable duration."""

        seconds = max(
            0,
            int(
                cls.number(value)
            ),
        )

        days, seconds = divmod(
            seconds,
            86400,
        )

        hours, seconds = divmod(
            seconds,
            3600,
        )

        minutes, seconds = divmod(
            seconds,
            60,
        )

        parts = []

        if days:
            parts.append(
                f"{days}d"
            )

        if hours:
            parts.append(
                f"{hours}h"
            )

        if minutes:
            parts.append(
                f"{minutes}m"
            )

        if (
            seconds
            or not parts
        ):
            parts.append(
                f"{seconds}s"
            )

        return " ".join(parts)

    @classmethod
    def format_ratio(
        cls,
        value,
    ):
        """Format a torrent share ratio."""

        ratio = cls.number(value)

        if ratio < 0:
            return "Unknown"

        if ratio >= 100:
            return "∞"

        return f"{ratio:.2f}"

    async def notify_completed(
        self,
        torrent,
        previous,
    ):
        """Send the formatted completion notification."""

        name = str(
            torrent.get(
                "name",
                previous.get(
                    "name",
                    "Unknown torrent",
                ),
            )
        )

        category = str(
            torrent.get(
                "category",
                previous.get(
                    "category",
                    "",
                ),
            )
            or "Uncategorized"
        )

        size = self.number(
            torrent.get(
                "size",
                0,
            )
        )

        downloaded = self.number(
            torrent.get(
                "downloaded",
                size,
            )
        )

        uploaded = self.number(
            torrent.get(
                "uploaded",
                0,
            )
        )

        active_time = self.number(
            torrent.get(
                "time_active",
                0,
            )
        )

        # Average speed is based on actual active time rather than
        # wall-clock time. qBittorrent exposes both values through
        # /torrents/info.
        average_speed = 0.0

        if active_time > 0:
            average_speed = (
                downloaded / active_time
            )

        peak_speed = max(
            self.number(
                previous.get(
                    "peak_download_speed",
                    0,
                )
            ),
            self.number(
                torrent.get(
                    "dlspeed",
                    0,
                )
            ),
        )

        ratio = self.number(
            torrent.get(
                "ratio",
                0,
            )
        )

        completion_on = self.integer(
            torrent.get(
                "completion_on",
                0,
            )
        )

        fields = [
            (
                "📦 Size",
                self.format_bytes(size),
                True,
            ),
            (
                "⏱ Download time",
                self.format_duration(
                    active_time
                ),
                True,
            ),
            (
                "🚀 Avg. speed",
                self.format_speed(
                    average_speed
                ),
                True,
            ),
            (
                "📈 Peak speed",
                self.format_speed(
                    peak_speed
                ),
                True,
            ),
            (
                "📥 Downloaded",
                self.format_bytes(
                    downloaded
                ),
                True,
            ),
            (
                "📤 Uploaded",
                self.format_bytes(
                    uploaded
                ),
                True,
            ),
            (
                "🔄 Ratio",
                self.format_ratio(
                    ratio
                ),
                True,
            ),
            (
                "📁 Category",
                category,
                True,
            ),
        ]

        user_id = str(
            getattr(
                settings,
                "DISCORD_USER_ID",
                "",
            )
            or ""
        ).strip()

        content = (
            f"<@{user_id}>"
            if user_id.isdigit()
            else None
        )

        description = (
            f"**{name}**"
        )

        if completion_on > 0:
            description += (
                f"\nCompleted at <t:{completion_on}:F>"
            )

        await self.notification_service.success(
            title="✅ Torrent Completed",
            description=description,
            fields=fields,
            content=content,
        )

    async def check(self):
        """Run one torrent monitoring cycle."""

        torrents = await self.service.get_torrents()

        if not isinstance(
            torrents,
            list,
        ):
            raise RuntimeError(
                "qBittorrent returned an invalid "
                "torrent list."
            )

        initial_scan = not self.state

        state_changed = False

        tracked = 0
        completed_count = 0
        active_count = 0

        for torrent in torrents:
            torrent_hash = torrent.get(
                "hash"
            )

            if not torrent_hash:
                logger.warning(
                    "[TorrentMonitor] Torrent without "
                    "hash received."
                )
                continue

            torrent_hash = str(
                torrent_hash
            ).lower()

            name = torrent.get(
                "name",
                "Unknown torrent",
            )

            category = torrent.get(
                "category",
                "",
            )

            completed = self.is_completed(
                torrent
            )

            download_speed = self.number(
                torrent.get(
                    "dlspeed",
                    0,
                )
            )

            tracked += 1

            if completed:
                completed_count += 1
            else:
                active_count += 1

            previous = self.state.get(
                torrent_hash
            )

            # First time this torrent has been seen.
            if previous is None:
                self.state[torrent_hash] = {
                    "name": name,
                    "category": category,
                    "completed": completed,
                    "notified": completed,
                    "added_on": self.integer(
                        torrent.get(
                            "added_on",
                            0,
                        )
                    ),
                    "peak_download_speed": (
                        download_speed
                    ),
                }

                state_changed = True
                continue

            previous["name"] = name
            previous["category"] = category

            added_on = self.integer(
                torrent.get(
                    "added_on",
                    0,
                )
            )

            if (
                added_on
                and previous.get(
                    "added_on"
                ) != added_on
            ):
                previous["added_on"] = added_on
                state_changed = True

            old_peak = self.number(
                previous.get(
                    "peak_download_speed",
                    0,
                )
            )

            if download_speed > old_peak:
                previous[
                    "peak_download_speed"
                ] = download_speed

                state_changed = True

            was_completed = bool(
                previous.get(
                    "completed",
                    False,
                )
            )

            # Detect the transition:
            #
            # incomplete -> completed
            if (
                completed
                and not was_completed
            ):
                logger.info(
                    "[TorrentMonitor] Torrent completed: %s",
                    name,
                )

                if not previous.get(
                    "notified",
                    False,
                ):
                    await self.notify_completed(
                        torrent,
                        previous,
                    )

                    previous[
                        "notified"
                    ] = True

                previous[
                    "completed"
                ] = True

                state_changed = True

                continue

            if was_completed != completed:
                previous[
                    "completed"
                ] = completed

                state_changed = True

            # If a torrent was previously completed but later becomes
            # incomplete again, allow a future completion notification.
            if (
                not completed
                and previous.get(
                    "notified",
                    False,
                )
            ):
                previous[
                    "notified"
                ] = False

                state_changed = True

        if state_changed:
            self.save_state()

        if initial_scan:
            logger.info(
                "[TorrentMonitor] Initial scan: "
                "%d torrents tracked, "
                "%d completed, "
                "%d active.",
                tracked,
                completed_count,
                active_count,
            )