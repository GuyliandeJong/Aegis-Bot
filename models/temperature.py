from dataclasses import dataclass


@dataclass
class Temperature:
    name: str
    reading: float
    status: str