from datetime import datetime, timezone

from monitors.base_monitor import BaseMonitor
from monitors.state import state
from services.cpu_service import CPUService
from utils.logger import logger


class CPUMonitor(BaseMonitor):
    """
    Monitors CPU usage and reports sustained high CPU usage.

    Warning:
        CPU usage stays at or above CPU_WARNING for the configured
        warning duration.

    Critical:
        CPU usage stays at or above CPU_CRITICAL for the configured
        critical duration.

    Recovery:
        CPU usage stays below the appropriate recovery threshold
        for the configured recovery duration.
    """

    def __init__(self, notification_service):
        self.service = CPUService()
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
        Check current CPU usage and process sustained conditions.
        """

        from config.settings import settings

        # Retrieve the current CPU usage.
        status = await self.service.get_cpu_status()
        usage = status.cpu.usage_percent

        # Retrieve the persisted CPU monitoring state.
        cpu_state = state.cpu

        current_status = cpu_state.get("status", "healthy")

        warning_since = self._parse_timestamp(
            cpu_state.get("warning_since")
        )

        critical_since = self._parse_timestamp(
            cpu_state.get("critical_since")
        )

        recovery_since = self._parse_timestamp(
            cpu_state.get("recovery_since")
        )

        now = self._now()

        logger.debug(
            "[CPUMonitor] CPU usage: %.1f%% | state: %s",
            usage,
            current_status,
        )

        # --------------------------------------------------------
        # CRITICAL CPU
        # --------------------------------------------------------

        if usage >= settings.CPU_CRITICAL:
            # CPU has entered the critical range.
            if critical_since is None:
                critical_since = now
                cpu_state["critical_since"] = self._timestamp(
                    critical_since
                )
                state.save()

                logger.info(
                    "[CPUMonitor] CPU critical timer started: %.1f%%",
                    usage,
                )

                return

            duration = (
                now - critical_since
            ).total_seconds()

            # Critical condition has not persisted long enough.
            if duration < settings.CPU_CRITICAL_DURATION:
                return

            # Already critical; don't repeatedly alert.
            if current_status == "critical":
                return

            # A warning condition has escalated into critical.
            await self.notification_service.critical(
                title="CPU Usage Critical",
                description=(
                    f"CPU usage has remained critically high "
                    f"for {settings.CPU_CRITICAL_DURATION // 60} minutes.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"**Threshold**\n"
                    f"`{settings.CPU_CRITICAL:.0f}%`"
                ),
            )

            cpu_state["status"] = "critical"
            cpu_state["warning_since"] = None
            cpu_state["critical_since"] = self._timestamp(
                critical_since
            )
            cpu_state["recovery_since"] = None

            state.save()

            logger.warning(
                "[CPUMonitor] CPU entered critical state: %.1f%%",
                usage,
            )

            return

        # --------------------------------------------------------
        # WARNING CPU
        # --------------------------------------------------------

        if usage >= settings.CPU_WARNING:
            # Critical timer is no longer valid because CPU has
            # dropped below the critical threshold.
            if critical_since is not None:
                cpu_state["critical_since"] = None
                critical_since = None

            # Start the warning timer if necessary.
            if warning_since is None:
                warning_since = now
                cpu_state["warning_since"] = self._timestamp(
                    warning_since
                )
                state.save()

                logger.info(
                    "[CPUMonitor] CPU warning timer started: %.1f%%",
                    usage,
                )

                return

            duration = (
                now - warning_since
            ).total_seconds()

            # Warning condition has not persisted long enough.
            if duration < settings.CPU_WARNING_DURATION:
                return

            # Already warning; don't repeatedly alert.
            if current_status == "warning":
                return

            await self.notification_service.warning(
                title="CPU Usage High",
                description=(
                    f"CPU usage has remained high "
                    f"for {settings.CPU_WARNING_DURATION // 60} minutes.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"**Threshold**\n"
                    f"`{settings.CPU_WARNING:.0f}%`"
                ),
            )

            cpu_state["status"] = "warning"
            cpu_state["warning_since"] = self._timestamp(
                warning_since
            )
            cpu_state["critical_since"] = None
            cpu_state["recovery_since"] = None

            state.save()

            logger.warning(
                "[CPUMonitor] CPU entered warning state: %.1f%%",
                usage,
            )

            return

        # --------------------------------------------------------
        # RECOVERY / HEALTHY
        # --------------------------------------------------------

        # CPU is below the warning threshold, so neither high-usage
        # timer remains valid.
        cpu_state["warning_since"] = None
        cpu_state["critical_since"] = None

        # Nothing needs recovering if we're already healthy.
        if current_status == "healthy":
            cpu_state["recovery_since"] = None
            state.save()
            return

        # Determine which recovery threshold applies.
        if current_status == "critical":
            recovery_threshold = (
                settings.CPU_CRITICAL_RECOVERY_THRESHOLD
            )
        else:
            recovery_threshold = (
                settings.CPU_RECOVERY_THRESHOLD
            )

        # CPU hasn't dropped far enough to begin recovery.
        if usage >= recovery_threshold:
            cpu_state["recovery_since"] = None
            state.save()
            return

        # Start recovery timer.
        if recovery_since is None:
            recovery_since = now
            cpu_state["recovery_since"] = self._timestamp(
                recovery_since
            )
            state.save()

            logger.info(
                "[CPUMonitor] CPU recovery timer started: %.1f%%",
                usage,
            )

            return

        duration = (
            now - recovery_since
        ).total_seconds()

        # Recovery condition has not persisted long enough.
        if duration < settings.CPU_RECOVERY_DURATION:
            return

        # Critical → warning
        if current_status == "critical":
            await self.notification_service.warning(
                title="CPU Usage Recovered",
                description=(
                    f"CPU usage has recovered from critical levels.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`\n\n"
                    f"CPU remains above the healthy range."
                ),
            )

            cpu_state["status"] = "warning"

        # Warning → healthy
        else:
            await self.notification_service.success(
                title="CPU Usage Normal",
                description=(
                    f"CPU usage has returned to normal levels.\n\n"
                    f"**Current Usage**\n"
                    f"`{usage:.1f}%`"
                ),
            )

            cpu_state["status"] = "healthy"

        cpu_state["warning_since"] = None
        cpu_state["critical_since"] = None
        cpu_state["recovery_since"] = None

        state.save()

        logger.info(
            "[CPUMonitor] CPU recovered: %.1f%%",
            usage,
        )