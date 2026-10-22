import socket

import psutil

from modules.host.models.network import Network
from modules.host.models.network_status import NetworkStatus


IGNORED_INTERFACES = (
    "lo",
    "docker",
    "veth",
    "br-",
    "virbr",
)


class NetworkService:
    async def get_network_status(self) -> NetworkStatus:
        hostname = socket.gethostname()

        interfaces = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)

        for interface, addresses in interfaces.items():
            if interface.startswith(IGNORED_INTERFACES):
                continue

            stat = stats.get(interface)

            if stat is None or not stat.isup:
                continue

            ipv4 = "Unknown"
            mac = "Unknown"

            for address in addresses:
                if address.family == socket.AF_INET:
                    ipv4 = address.address

                elif getattr(psutil, "AF_LINK", object()) == address.family:
                    mac = address.address

            counters = io.get(interface)

            return NetworkStatus(
                hostname=hostname,
                network=Network(
                    interface=interface,
                    ipv4=ipv4,
                    mac_address=mac,
                    is_up=stat.isup,
                    speed=stat.speed,
                    bytes_sent=counters.bytes_sent if counters else 0,
                    bytes_received=counters.bytes_recv if counters else 0,
                ),
            )

        return NetworkStatus(
            hostname=hostname,
            network=Network(
                interface="Unknown",
                ipv4="Unknown",
                mac_address="Unknown",
                is_up=False,
                speed=0,
                bytes_sent=0,
                bytes_received=0,
            ),
        )