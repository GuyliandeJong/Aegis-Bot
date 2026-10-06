from dataclasses import dataclass


@dataclass
class IdracStatus:
    hostname: str
    firmware_version: str
    ip_address: str
    mac_address: str
    health: str