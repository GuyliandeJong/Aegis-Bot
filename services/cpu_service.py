import psutil
from cpuinfo import get_cpu_info

from models.cpu import CPU
from models.cpu_status import CPUStatus
from utils.formatting import format_uptime


class CPUService:
    async def get_cpu_status(self) -> CPUStatus:
        info = get_cpu_info()

        processor = (
            info.get("brand_raw", "Unknown")
            .replace("(R)", "")
            .replace("(TM)", "")
            .replace(" CPU", "")
            .split("@")[0]
            .replace("  ", " ")
            .strip()
        )

        frequency = psutil.cpu_freq()

        return CPUStatus(
            cpu=CPU(
                processor=processor,
                usage_percent=psutil.cpu_percent(interval=1),
                current_frequency=(
                    round(frequency.current / 1000, 2)
                    if frequency
                    else 0.0
                ),
                physical_cores=psutil.cpu_count(logical=False) or 0,
                logical_cores=psutil.cpu_count(logical=True) or 0,
                uptime=format_uptime(psutil.boot_time()),
            )
        )