from dataclasses import dataclass

from models.memory import Memory


@dataclass
class MemoryStatus:
    memory: Memory