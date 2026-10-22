from dataclasses import dataclass

from modules.idrac.models.temperature import Temperature


@dataclass
class TemperatureStatus:
    temperatures: list[Temperature]