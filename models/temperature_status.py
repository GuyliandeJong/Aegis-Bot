from dataclasses import dataclass

from models.temperature import Temperature


@dataclass
class TemperatureStatus:
    temperatures: list[Temperature]