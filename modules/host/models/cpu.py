from dataclasses import dataclass


@dataclass
class CPU:
    processor: str
    usage_percent: float
    current_frequency: float
    physical_cores: int
    logical_cores: int
    uptime: str