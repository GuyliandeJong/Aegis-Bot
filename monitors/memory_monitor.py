from datetime import datetime, timezone

from monitors.base_monitor import BaseMonitor
from monitors.state import state
from services.memory_service import MemoryService
from utils.logger import logger
from config.settings import settings

class MemoryMonitor(BaseMonitor):
    """
    Monitors system memory usage and reports sustained high usage.

    Warning:
        Memory usage stays at or above MEMORY_WARNING for the
        configured warning duration.

    Critical:
        Memory usage stays at or above MEMORY_CRITICAL for the
        configured critical duration.

    Recovery:
        Memory usage stays below the appropriate recovery threshold
        for the configured recovery duration.
    """

    def __init__(self, notification_service):
        self.service = MemoryService()
        self.notification_service = notification_service

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
        Check current memory usage and process sustained conditions.
        """

        # Retrieve the current memory information.
        status = await self.service.get_memory_status()
        memory = status.memory
        usage = memory.percent_used

        # Retrieve the persisted memory monitoring state.
        memory_state = state.memory

        current_status = memory_state.get("status", "healthy")

        warning_since = self._parse_timestamp(
            memory_state.get("warning_since")
        )

        critical_since = self._parse_timestamp(
            memory_state.get("critical_since")
        )

        recovery_since = self._parse_timestamp(
            memory_state.get("recovery_since")
        )

        now = self._now()

        logger.debug(
            "[MemoryMonitor] Memory usage: %.1f%% | state: %s",
            usage,
            current_status,
        )

        # --------------------------------------------------------
        # CRITICAL MEMORY
        # --------------------------------------------------------

        if usage >= settings.MEMORY_CRITICAL:
            # Memory has entered the critical range.
            if critical_since is None:
                critical_since = now
                memory_state["critical_since"] = self._timestamp(
                    critical_since
                )
                state.save()

                logger.info(
                    "[MemoryMonitor] Memory critical timer started: %.1f%%",
                    usage,
                )

                return

            duration = (
                now - critical_since
            ).total_seconds()

            # Critical condition has not persisted long enough.
            if duration < settings.MEMORY_CRITICAL_DURATION:
                return

            # Already critical; don't repeatedly alert.
            if current_status == "critical":
                return

            await self.notification_service.critical(
                title="Memory Usage Critical",
                description=(
                    f"Memory usage has remained critically high "
                    f"for {settings.MEMORY_CRITICAL_DURATION // 60} minutes.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"**Threshold**\n"
                    f"`{settings.MEMORY_CRITICAL:.0f}%`\n\n"
                    f"**Used**\n"
                    f"`{memory.used}`\n\n"
                    f"**Available**\n"
                    f"`{memory.available}`\n\n"
                    f"**Total**\n"
                    f"`{memory.total}`"
                ),
            )

            memory_state["status"] = "critical"
            memory_state["warning_since"] = None
            memory_state["critical_since"] = self._timestamp(
                critical_since
            )
            memory_state["recovery_since"] = None

            state.save()

            logger.warning(
                "[MemoryMonitor] Memory entered critical state: %.1f%%",
                usage,
            )

            return

        # --------------------------------------------------------
        # WARNING MEMORY
        # --------------------------------------------------------

        if usage >= settings.MEMORY_WARNING:
            # Critical timer is no longer valid because memory has
            # dropped below the critical threshold.
            if critical_since is not None:
                memory_state["critical_since"] = None
                critical_since = None

            # Start the warning timer if necessary.
            if warning_since is None:
                warning_since = now
                memory_state["warning_since"] = self._timestamp(
                    warning_since
                )
                state.save()

                logger.info(
                    "[MemoryMonitor] Memory warning timer started: %.1f%%",
                    usage,
                )

                return

            duration = (
                now - warning_since
            ).total_seconds()

            # Warning condition has not persisted long enough.
            if duration < settings.MEMORY_WARNING_DURATION:
                return

            # Already warning; don't repeatedly alert.
            if current_status == "warning":
                return

            await self.notification_service.warning(
                title="Memory Usage High",
                description=(
                    f"Memory usage has remained high "
                    f"for {settings.MEMORY_WARNING_DURATION // 60} minutes.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"**Threshold**\n"
                    f"`{settings.MEMORY_WARNING:.0f}%`\n\n"
                    f"**Used**\n"
                    f"`{memory.used}`\n\n"
                    f"**Available**\n"
                    f"`{memory.available}`\n\n"
                    f"**Total**\n"
                    f"`{memory.total}`"
                ),
            )

            memory_state["status"] = "warning"
            memory_state["warning_since"] = self._timestamp(
                warning_since
            )
            memory_state["critical_since"] = None
            memory_state["recovery_since"] = None

            state.save()

            logger.warning(
                "[MemoryMonitor] Memory entered warning state: %.1f%%",
                usage,
            )

            return

        # --------------------------------------------------------
        # RECOVERY / HEALTHY
        # --------------------------------------------------------

        # Memory is below the warning threshold, so neither high-usage
        # timer remains valid.
        memory_state["warning_since"] = None
        memory_state["critical_since"] = None

        # Nothing needs recovering if we're already healthy.
        if current_status == "healthy":
            memory_state["recovery_since"] = None
            state.save()
            return

        # Determine which recovery threshold applies.
        if current_status == "critical":
            recovery_threshold = (
                settings.MEMORY_CRITICAL_RECOVERY_THRESHOLD
            )
        else:
            recovery_threshold = (
                settings.MEMORY_RECOVERY_THRESHOLD
            )

        # Memory hasn't dropped far enough to begin recovery.
        if usage >= recovery_threshold:
            memory_state["recovery_since"] = None
            state.save()
            return

        # Start recovery timer.
        if recovery_since is None:
            recovery_since = now
            memory_state["recovery_since"] = self._timestamp(
                recovery_since
            )
            state.save()

            logger.info(
                "[MemoryMonitor] Memory recovery timer started: %.1f%%",
                usage,
            )

            return

        duration = (
            now - recovery_since
        ).total_seconds()

        # Recovery condition has not persisted long enough.
        if duration < settings.MEMORY_RECOVERY_DURATION:
            return

        # Critical → warning
        if current_status == "critical":
            await self.notification_service.warning(
                title="Memory Usage Recovered",
                description=(
                    f"Memory usage has recovered from critical levels.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"Memory remains above the healthy range."
                ),
            )

            memory_state["status"] = "warning"

        # Warning → healthy
        else:
            await self.notification_service.success(
                title="Memory Usage Normal",
                description=(
                    f"Memory usage has returned to normal levels.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`"
                ),
            )

            memory_state["status"] = "healthy"

        memory_state["warning_since"] = None
        memory_state["critical_since"] = None
        memory_state["recovery_since"] = None

        state.save()

        logger.info(
            "[MemoryMonitor] Memory recovered: %.1f%%",
            usage,
        )