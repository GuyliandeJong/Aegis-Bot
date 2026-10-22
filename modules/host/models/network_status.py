from dataclasses import dataclass

from modules.host.models.network import Network


@dataclass
class NetworkStatus:
    hostname: str
    network: Network