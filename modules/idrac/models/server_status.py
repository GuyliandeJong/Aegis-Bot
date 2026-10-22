from dataclasses import dataclass


@dataclass
class ServerStatus:
    manufacturer: str
    model: str
    service_tag: str
    bios_version: str

    power_state: str
    health: str

    cpu_model: str
    total_memory_gb: int

    operating_system: str
    operating_system_version: str