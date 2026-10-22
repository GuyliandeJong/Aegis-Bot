from datetime import datetime, timezone

from config.settings import settings
from core.base_monitor import BaseMonitor
from core.state import state
from utils.logger import logger


class ThresholdMonitor(BaseMonitor):
    """
    Shared logic for monitors that watch one usage percentage.

    Warning:
        Usage stays at or above <PREFIX>_WARNING for
        <PREFIX>_WARNING_DURATION seconds.

    Critical:
        Usage stays at or above <PREFIX>_CRITICAL for
        <PREFIX>_CRITICAL_DURATION seconds.

    Recovery:
        Usage stays below the appropriate recovery threshold for
        <PREFIX>_RECOVERY_DURATION seconds.

    Subclasses set LABEL, SETTINGS_PREFIX and STATE_NAME, and
    implement _read().
    """

    # Name used in notifications and log messages, e.g. "CPU".
    LABEL = ""

    # Prefix of this monitor's values in config/settings.py, e.g. "CPU".
    SETTINGS_PREFIX = ""

    # Attribute on the shared MonitorState holding this monitor's state.
    STATE_NAME = ""

    def __init__(self, notification_service):
        self.notification_service = notification_service

    async def _read(self) -> tuple[float, str]:
        """
        Return the current usage percentage and any extra detail text
        to append to warning and critical notifications.
        """
        raise NotImplementedError

    def _setting(self, name: str):
        """Return one of this monitor's configured values."""
        return getattr(
            settings,
            f"{self.SETTINGS_PREFIX}_{name}",
        )

    @staticmethod
    def _now() -> datetime:
        """
        Return the current UTC time.
        """
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_timestamp(value):
        """
        Convert a stored ISO timestamp back into a datetime.
        """
        if not value:
            return None

        return datetime.fromisoformat(value)

    @staticmethod
    def _timestamp(value):
        """
        Convert a datetime into a persistent ISO timestamp.
        """
        return value.isoformat() if value else None

    async def check(self):
        """
        Check current usage and process sustained conditions.
        """
        label = self.LABEL
        tag = f"[{self.__class__.__name__}]"

        usage, details = await self._read()

        # Retrieve the persisted monitoring state.
        monitor_state = getattr(state, self.STATE_NAME)

        current_status = monitor_state.get("status", "healthy")

        warning_since = self._parse_timestamp(
            monitor_state.get("warning_since")
        )

        critical_since = self._parse_timestamp(
            monitor_state.get("critical_since")
        )

        recovery_since = self._parse_timestamp(
            monitor_state.get("recovery_since")
        )

        now = self._now()

        logger.debug(
            "%s %s usage: %.1f%% | state: %s",
            tag,
            label,
            usage,
            current_status,
        )

        # --------------------------------------------------------
        # CRITICAL
        # --------------------------------------------------------

        critical_threshold = self._setting("CRITICAL")

        if usage >= critical_threshold:
            # Usage has entered the critical range.
            if critical_since is None:
                monitor_state["critical_since"] = self._timestamp(now)
                state.save()

                logger.info(
                    "%s %s critical timer started: %.1f%%",
                    tag,
                    label,
                    usage,
                )

                return

            duration = (
                now - critical_since
            ).total_seconds()

            critical_duration = self._setting("CRITICAL_DURATION")

            # Critical condition has not persisted long enough.
            if duration < critical_duration:
                return

            # Already critical; don't repeatedly alert.
            if current_status == "critical":
                return

            await self.notification_service.critical(
                title=f"{label} Usage Critical",
                description=(
                    f"{label} usage has remained critically high "
                    f"for {critical_duration // 60} minutes.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"**Threshold**\n"
                    f"`{critical_threshold:.0f}%`"
                    f"{details}"
                ),
            )

            monitor_state["status"] = "critical"
            monitor_state["warning_since"] = None
            monitor_state["critical_since"] = self._timestamp(
                critical_since
            )
            monitor_state["recovery_since"] = None

            state.save()

            logger.warning(
                "%s %s entered critical state: %.1f%%",
                tag,
                label,
                usage,
            )

            return

        # --------------------------------------------------------
        # WARNING
        # --------------------------------------------------------

        warning_threshold = self._setting("WARNING")

        if usage >= warning_threshold:
            # Critical timer is no longer valid because usage has
            # dropped below the critical threshold.
            if critical_since is not None:
                monitor_state["critical_since"] = None

            # Start the warning timer if necessary.
            if warning_since is None:
                monitor_state["warning_since"] = self._timestamp(now)
                state.save()

                logger.info(
                    "%s %s warning timer started: %.1f%%",
                    tag,
                    label,
                    usage,
                )

                return

            duration = (
                now - warning_since
            ).total_seconds()

            warning_duration = self._setting("WARNING_DURATION")

            # Warning condition has not persisted long enough.
            if duration < warning_duration:
                return

            # Already warning; don't repeatedly alert.
            if current_status == "warning":
                return

            await self.notification_service.warning(
                title=f"{label} Usage High",
                description=(
                    f"{label} usage has remained high "
                    f"for {warning_duration // 60} minutes.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"**Threshold**\n"
                    f"`{warning_threshold:.0f}%`"
                    f"{details}"
                ),
            )

            monitor_state["status"] = "warning"
            monitor_state["warning_since"] = self._timestamp(
                warning_since
            )
            monitor_state["critical_since"] = None
            monitor_state["recovery_since"] = None

            state.save()

            logger.warning(
                "%s %s entered warning state: %.1f%%",
                tag,
                label,
                usage,
            )

            return

        # --------------------------------------------------------
        # RECOVERY / HEALTHY
        # --------------------------------------------------------

        # Usage is below the warning threshold, so neither high-usage
        # timer remains valid.
        monitor_state["warning_since"] = None
        monitor_state["critical_since"] = None

        # Nothing needs recovering if we're already healthy.
        if current_status == "healthy":
            monitor_state["recovery_since"] = None
            state.save()
            return

        # Determine which recovery threshold applies.
        if current_status == "critical":
            recovery_threshold = self._setting(
                "CRITICAL_RECOVERY_THRESHOLD"
            )
        else:
            recovery_threshold = self._setting(
                "RECOVERY_THRESHOLD"
            )

        # Usage hasn't dropped far enough to begin recovery.
        if usage >= recovery_threshold:
            monitor_state["recovery_since"] = None
            state.save()
            return

        # Start recovery timer.
        if recovery_since is None:
            monitor_state["recovery_since"] = self._timestamp(now)
            state.save()

            logger.info(
                "%s %s recovery timer started: %.1f%%",
                tag,
                label,
                usage,
            )

            return

        duration = (
            now - recovery_since
        ).total_seconds()

        # Recovery condition has not persisted long enough.
        if duration < self._setting("RECOVERY_DURATION"):
            return

        # Critical → warning
        if current_status == "critical":
            await self.notification_service.warning(
                title=f"{label} Usage Recovered",
                description=(
                    f"{label} usage has recovered from critical levels.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"{label} remains above the healthy range."
                ),
            )

            monitor_state["status"] = "warning"

        # Warning → healthy
        else:
            await self.notification_service.success(
                title=f"{label} Usage Normal",
                description=(
                    f"{label} usage has returned to normal levels.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`"
                ),
            )

            monitor_state["status"] = "healthy"

        monitor_state["warning_since"] = None
        monitor_state["critical_since"] = None
        monitor_state["recovery_since"] = None

        state.save()

        logger.info(
            "%s %s recovered: %.1f%%",
            tag,
            label,
            usage,
        )
