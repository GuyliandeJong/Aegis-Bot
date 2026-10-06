from dataclasses import dataclass

from models.cpu import CPU


@dataclass
class CPUStatus:
    cpu: CPU