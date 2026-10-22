import psutil

from modules.host.models.memory import Memory
from modules.host.models.memory_status import MemoryStatus
from utils.formatting import format_capacity


class MemoryService:
    async def get_memory_status(self) -> MemoryStatus:
        virtual = psutil.virtual_memory()
        swap = psutil.swap_memory()

        cached = getattr(virtual, "cached", 0)

        swap_percent = (
            0.0
            if swap.total == 0
            else round((swap.used / swap.total) * 100, 1)
        )

        return MemoryStatus(
            memory=Memory(
                total=format_capacity(virtual.total),
                used=format_capacity(virtual.used),
                available=format_capacity(virtual.available),
                cached=format_capacity(cached),
                percent_used=virtual.percent,
                swap_total=format_capacity(swap.total),
                swap_used=format_capacity(swap.used),
                swap_percent=swap_percent,
            )
        )