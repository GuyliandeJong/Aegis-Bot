from monitors.threshold_monitor import ThresholdMonitor
from services.cpu_service import CPUService


class CPUMonitor(ThresholdMonitor):
    """
    Monitors CPU usage and reports sustained high CPU usage.

    The thresholds, durations and recovery rules are described in
    ThresholdMonitor and configured through the CPU_* settings.
    """

    LABEL = "CPU"
    SETTINGS_PREFIX = "CPU"
    STATE_NAME = "cpu"

    def __init__(self, notification_service):
        super().__init__(notification_service)
        self.service = CPUService()

    async def _read(self) -> tuple[float, str]:
        status = await self.service.get_cpu_status()

        return status.cpu.usage_percent, ""
