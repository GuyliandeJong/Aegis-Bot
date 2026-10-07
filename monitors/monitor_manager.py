import asyncio

from config.settings import settings
from services.notification_service import NotificationService
from utils.logger import logger


class MonitorManager:
    """
    Coordinates all monitoring tasks.

    Each registered monitor runs independently. Monitors may optionally
    define their own `INTERVAL` attribute; otherwise the global monitor
    interval is used.
    """

    def __init__(self, bot):
        self.bot = bot
        self.monitors = []

        self.notification_service = NotificationService(
            bot
        )

    def register(self, monitor):
        """Add a monitor to the monitoring framework."""

        self.monitors.append(
            monitor
        )

        logger.debug(
            "[MonitorManager] Registered monitor: %s",
            monitor.__class__.__name__,
        )

    async def _run_monitor(self, monitor):
        """Execute one monitor check with timeout and isolation."""

        monitor_name = monitor.__class__.__name__

        # A monitor may allow itself longer than the global timeout,
        # for example one that scans whole Jellyfin libraries.
        timeout = getattr(
            monitor,
            "TIMEOUT",
            settings.MONITOR_TIMEOUT,
        )

        try:
            await asyncio.wait_for(
                monitor.check(),
                timeout=timeout,
            )

        except asyncio.TimeoutError:
            logger.error(
                "[MonitorManager] Monitor %s timed out after %ss.",
                monitor_name,
                timeout,
            )

        except asyncio.CancelledError:
            raise

        except Exception as ex:
            logger.exception(
                "[MonitorManager] Monitor %s failed: %s",
                monitor_name,
                ex,
            )

    async def _monitor_loop(self, monitor):
        """
        Run one monitor independently on its configured interval.

        The interval is measured between completed checks, so a slow
        check does not cause overlapping executions of the same monitor.
        """

        monitor_name = monitor.__class__.__name__

        try:
            interval = float(
                getattr(
                    monitor,
                    "INTERVAL",
                    settings.MONITOR_INTERVAL,
                )
            )
        except (TypeError, ValueError):
            interval = float(
                settings.MONITOR_INTERVAL
            )

        interval = max(
            1.0,
            interval,
        )

        logger.info(
            "[MonitorManager] %s started (interval: %ss).",
            monitor_name,
            int(interval)
            if interval.is_integer()
            else interval,
        )

        while True:
            started_at = asyncio.get_running_loop().time()

            await self._run_monitor(
                monitor
            )

            elapsed = (
                asyncio.get_running_loop().time()
                - started_at
            )

            await asyncio.sleep(
                max(
                    0,
                    interval - elapsed,
                )
            )

    async def run(self):
        """
        Start one independent monitoring loop per registered monitor.

        This preserves the global 60-second interval for normal monitors
        while allowing specialized monitors such as TorrentMonitor to
        sample more frequently.
        """

        logger.info(
            "[MonitorManager] Monitoring framework started "
            "(monitors: %s).",
            len(self.monitors),
        )

        tasks = [
            asyncio.create_task(
                self._monitor_loop(monitor)
            )
            for monitor in self.monitors
        ]

        if not tasks:
            logger.warning(
                "[MonitorManager] No monitors are registered."
            )
            return

        try:
            await asyncio.gather(
                *tasks
            )

        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()

            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

            raise