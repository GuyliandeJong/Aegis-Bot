"""Formatting helpers for torrent information."""
from datetime import timedelta

from commands.torrent.constants import STATE_LABELS
from utils.formatting import format_bytes


class TorrentFormattingMixin:
    """Mixin containing torrent formatting helpers."""
    @staticmethod
    def _format_bytes(value: int | float | None) -> str:
        """Format a byte value into a readable size."""
        return format_bytes(value)
    @staticmethod
    def _format_speed(value: int | float | None) -> str:
        """Format a transfer speed."""
        return (
            TorrentFormattingMixin._format_bytes(value)
            + "/s"
        )
    @staticmethod
    def _format_eta(value: int | float | None) -> str:
        """Format qBittorrent ETA seconds."""
        if value is None:
            return "Unknown"
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return "Unknown"
        if seconds < 0:
            return "Unknown"
        # qBittorrent uses a very large value to indicate
        # an unavailable/infinite ETA.
        if seconds >= 8640000:
            return "Unknown"
        delta = timedelta(
            seconds=seconds
        )
        days = delta.days
        hours, remainder = divmod(
            delta.seconds,
            3600,
        )
        minutes, seconds = divmod(
            remainder,
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
        if seconds or not parts:
            parts.append(
                f"{seconds}s"
            )
        return " ".join(parts)
    @staticmethod
    def _format_progress_bar(
        progress: int | float | None,
        width: int = 12,
    ) -> str:
        """
        Build a compact textual progress bar.
        Example:
            ████████░░░░ 67.4%
        """
        try:
            progress = float(progress or 0)
        except (TypeError, ValueError):
            progress = 0.0
        progress = max(
            0.0,
            min(
                1.0,
                progress,
            ),
        )
        try:
            width = int(width)
        except (TypeError, ValueError):
            width = 12
        width = max(
            1,
            width,
        )
        filled = round(
            progress * width
        )
        filled = max(
            0,
            min(
                width,
                filled,
            ),
        )
        empty = width - filled
        return (
            f"{'█' * filled}"
            f"{'░' * empty}"
            f" {progress * 100:.1f}%"
        )
    @staticmethod
    def _format_state(state: str | None) -> str:
        """
        Convert qBittorrent's internal state names into readable text.
        """
        if not state:
            return "Unknown"
        normalized = str(
            state
        ).strip().lower()
        if normalized in STATE_LABELS:
            return STATE_LABELS[normalized]
        return normalized.replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        ).title()
    @staticmethod
    def _format_added_time(
        value: int | float | None,
    ) -> str:
        """
        Format qBittorrent's Unix timestamp as a Discord timestamp.
        Discord renders this relative to the viewer, for example:
            2 hours ago
        If the timestamp cannot be parsed, return Unknown.
        """
        if value is None:
            return "Unknown"
        try:
            timestamp = int(float(value))
        except (TypeError, ValueError):
            return "Unknown"
        if timestamp <= 0:
            return "Unknown"
        return f"<t:{timestamp}:R>"
    @staticmethod
    def _format_added_time_absolute(
        value: int | float | None,
    ) -> str:
        """
        Format qBittorrent's Unix timestamp as a Discord absolute timestamp.
        Discord renders the actual date/time in the user's local timezone.
        """
        if value is None:
            return "Unknown"
        try:
            timestamp = int(float(value))
        except (TypeError, ValueError):
            return "Unknown"
        if timestamp <= 0:
            return "Unknown"
        return f"<t:{timestamp}:f>"
    @staticmethod
    def _truncate_name(
        value: str,
        maximum: int = 100,
    ) -> str:
        """Truncate a long torrent name."""
        value = str(value)
        if len(value) <= maximum:
            return value
        if maximum <= 3:
            return value[:maximum]
        return value[: maximum - 3] + "..."