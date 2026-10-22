from dataclasses import dataclass

from modules.host.models.memory import Memory


@dataclass
class MemoryStatus:
    memory: Memory