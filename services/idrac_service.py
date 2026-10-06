from models.idrac_status import IdracStatus
from redfish.client import RedfishClient


class IdracService:
    def __init__(self):
        self.client = RedfishClient()

    async def get_status(self) -> IdracStatus:
        manager = await self.client.get_manager()
        attributes = await self.client.get_idrac_attributes()

        attrs = attributes.get("Attributes", {})

        return IdracStatus(
            hostname=attrs.get("CurrentNIC.1.DNSRacName", "Unknown"),
            firmware_version=manager.get("FirmwareVersion", "Unknown"),
            ip_address=attrs.get("CurrentIPv4.1.Address", "Unknown"),
            mac_address=attrs.get("CurrentNIC.1.MACAddress", "Unknown"),
            health=manager.get("Status", {}).get("Health", "Unknown"),
        )

    async def close(self):
        await self.client.close()