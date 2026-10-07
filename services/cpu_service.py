import asyncio

import psutil
from cpuinfo import get_cpu_info

from models.cpu import CPU
from models.cpu_status import CPUStatus
from utils.formatting import format_uptime


class CPUService:
    # The processor model cannot change while the bot is running, and
    # py-cpuinfo is slow, so it is only looked up once.
    _processor: str | None = None

    @staticmethod
    def _read_processor() -> str:
        info = get_cpu_info()

        return (
            info.get("brand_raw", "Unknown")
            .replace("(R)", "")
            .replace("(TM)", "")
            .replace(" CPU", "")
            .split("@")[0]
            .replace("  ", " ")
            .strip()
        )

    async def get_cpu_status(self) -> CPUStatus:
        # Both calls below block for a second or more. Run them in a
        # worker thread so the Discord connection and the other
        # monitors keep running in the meantime.
        if CPUService._processor is None:
            CPUService._processor = await asyncio.to_thread(
                self._read_processor
            )

        usage_percent = await asyncio.to_thread(
            psutil.cpu_percent,
            1,
        )

        frequency = psutil.cpu_freq()

        return CPUStatus(
            cpu=CPU(
                processor=CPUService._processor,
                usage_percent=usage_percent,
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
