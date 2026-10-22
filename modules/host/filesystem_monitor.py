from config.settings import settings
from core.base_monitor import BaseMonitor
from core.state import state
from modules.host.filesystem_service import FilesystemService
from utils.logger import logger


class FilesystemMonitor(BaseMonitor):
    """
    Monitors filesystem usage and reports state changes.

    Each filesystem is assigned one of three states:

        healthy -> warning -> critical

    Notifications are only sent when the state changes.
    """

    def __init__(self, notification_service):
        self.service = FilesystemService()
        self.notification_service = notification_service

    @staticmethod
    def get_status(percent_used: int) -> str:
        """
        Determine the filesystem state based on usage percentage.
        """

        # Critical takes priority over warning.
        if percent_used >= settings.FILESYSTEM_CRITICAL:
            return "critical"

        # Usage at or above the warning threshold requires attention.
        if percent_used >= settings.FILESYSTEM_WARNING:
            return "warning"

        # Everything below the warning threshold is healthy.
        return "healthy"

    async def check(self):
        """
        Check all filesystems and process any state changes.
        """

        # Retrieve the current filesystem information.
        status = await self.service.get_filesystems()

        for filesystem in status.filesystems:
            current = self.get_status(filesystem.percent_used)

            # Retrieve the state that was last reported.
            previous = state.filesystems.get(filesystem.mountpoint)

            # Nothing changed, so no notification or log entry is needed.
            if previous == current:
                continue

            alert_key = f"filesystem:{filesystem.mountpoint}"

            # Usage hovering around a threshold would otherwise send a
            # notification on every crossing. Stay quiet until the
            # cooldown has passed; the reported state is left unchanged,
            # so a change that persists is still reported afterwards.
            # A filesystem becoming critical is always reported.
            if (
                previous is not None
                and current != "critical"
                and not state.alert_allowed(
                    alert_key,
                    self.alert_cooldown(),
                )
            ):
                logger.debug(
                    "[FilesystemMonitor] %s: %s -> %s suppressed "
                    "by alert cooldown.",
                    filesystem.mountpoint,
                    previous,
                    current,
                )
                continue

            # Record the state transition in the application log.
            logger.info(
                "[FilesystemMonitor] %s: %s -> %s (%s%%)",
                filesystem.mountpoint,
                previous or "unknown",
                current,
                filesystem.percent_used,
            )

            # On the first run, establish the current state without
            # sending a notification because there was no previous
            # state to recover from or alert about.
            if previous is None:
                state.filesystems[filesystem.mountpoint] = current
                state.save()
                continue

            # The filesystem has returned below the warning threshold.
            if current == "healthy":
                await self.notification_service.recovered(
                    title=f"Filesystem Recovered — {filesystem.mountpoint}",
                    description=(
                        f"**Mountpoint**\n"
                        f"{filesystem.mountpoint}\n\n"

                        f"**Current Usage**\n"
                        f"{filesystem.percent_used}%\n\n"

                        f"**Recovery Threshold**\n"
                        f"{settings.FILESYSTEM_WARNING}%\n\n"

                        f"**Used**\n"
                        f"{filesystem.used}\n\n"

                        f"**Available**\n"
                        f"{filesystem.free}\n\n"

                        f"**Total**\n"
                        f"{filesystem.total}"
                    ),
                )

            # The filesystem has crossed the warning threshold.
            elif current == "warning":
                await self.notification_service.warning(
                    title=f"Filesystem Warning — {filesystem.mountpoint}",
                    description=(
                        f"**Mountpoint**\n"
                        f"{filesystem.mountpoint}\n\n"

                        f"**Current Usage**\n"
                        f"{filesystem.percent_used}%\n\n"

                        f"**Warning Threshold**\n"
                        f"{settings.FILESYSTEM_WARNING}%\n\n"

                        f"**Used**\n"
                        f"{filesystem.used}\n\n"

                        f"**Available**\n"
                        f"{filesystem.free}\n\n"

                        f"**Total**\n"
                        f"{filesystem.total}"
                    ),
                )

            # The filesystem has crossed the critical threshold.
            elif current == "critical":
                await self.notification_service.critical(
                    title=f"Filesystem Critical — {filesystem.mountpoint}",
                    description=(
                        f"**Mountpoint**\n"
                        f"{filesystem.mountpoint}\n\n"

                        f"**Current Usage**\n"
                        f"{filesystem.percent_used}%\n\n"

                        f"**Critical Threshold**\n"
                        f"{settings.FILESYSTEM_CRITICAL}%\n\n"

                        f"**Used**\n"
                        f"{filesystem.used}\n\n"

                        f"**Available**\n"
                        f"{filesystem.free}\n\n"

                        f"**Total**\n"
                        f"{filesystem.total}"
                    ),
                )

            # Save the new state so the same alert is not repeatedly
            # sent during subsequent monitoring cycles.
            state.filesystems[filesystem.mountpoint] = current
            state.mark_alert(alert_key)
            state.save()