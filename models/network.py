from dataclasses import dataclass


@dataclass
class Network:
    interface: str
    ipv4: str
    mac_address: str
    is_up: bool
    speed: int
    bytes_sent: int
    bytes_received: int