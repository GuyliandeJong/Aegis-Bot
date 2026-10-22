from dataclasses import dataclass

from modules.host.models.cpu import CPU


@dataclass
class CPUStatus:
    cpu: CPU