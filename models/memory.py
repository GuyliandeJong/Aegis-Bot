from dataclasses import dataclass


@dataclass
class Memory:
    total: str
    used: str
    available: str
    cached: str
    percent_used: float

    swap_total: str
    swap_used: str
    swap_percent: float