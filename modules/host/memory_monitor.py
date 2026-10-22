from core.threshold_monitor import ThresholdMonitor
from modules.host.memory_service import MemoryService


class MemoryMonitor(ThresholdMonitor):
    """
    Monitors system memory usage and reports sustained high usage.

    The thresholds, durations and recovery rules are described in
    ThresholdMonitor and configured through the MEMORY_* settings.
    """

    LABEL = "Memory"
    SETTINGS_PREFIX = "MEMORY"
    STATE_NAME = "memory"

    def __init__(self, notification_service):
        super().__init__(notification_service)
        self.service = MemoryService()

    async def _read(self) -> tuple[float, str]:
        status = await self.service.get_memory_status()
        memory = status.memory

        details = (
            f"\n\n"
            f"**Used**\n"
            f"`{memory.used}`\n\n"
            f"**Available**\n"
            f"`{memory.available}`\n\n"
            f"**Total**\n"
            f"`{memory.total}`"
        )

        return memory.percent_used, details
