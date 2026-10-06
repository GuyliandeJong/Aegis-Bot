from dataclasses import dataclass

from models.network import Network


@dataclass
class NetworkStatus:
    hostname: str
    network: Network