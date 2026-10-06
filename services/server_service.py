from models.server_status import ServerStatus
from redfish.client import RedfishClient


class ServerService:

    def __init__(self):
        self.client = RedfishClient()

    async def get_status(self) -> ServerStatus:
        system = await self.client.get_system()
        attributes = await self.client.get_system_attributes()

        total_memory_gb = system.get("MemorySummary", {}).get("TotalSystemMemoryGiB", 0)

        processor = system.get("ProcessorSummary", {}).get("Model", "Unknown")

        dell_attributes = attributes.get("Attributes", {})

        operating_system = dell_attributes.get(
            "ServerOS.1.OSName",
            "Unknown"
        )

        operating_system_version = dell_attributes.get(
            "ServerOS.1.OSVersion",
            "Unknown"
        )

        return ServerStatus(
            manufacturer=system.get("Manufacturer", "Unknown"),
            model=system.get("Model", "Unknown"),
            service_tag=system.get("SKU", "Unknown"),
            bios_version=system.get("BiosVersion", "Unknown"),
            power_state=system.get("PowerState", "Unknown"),
            health=system.get("Status", {}).get("Health", "Unknown"),
            cpu_model=processor,
            total_memory_gb=total_memory_gb,
            operating_system=operating_system,
            operating_system_version=operating_system_version,
        )

    async def close(self):
        await self.client.close()