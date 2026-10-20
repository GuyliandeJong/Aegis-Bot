import aiohttp

from config.settings import settings
from utils.logger import logger


class RedfishClient:
    """Simple asynchronous Redfish client."""

    def __init__(self):
        self.base_url = f"https://{settings.IDRAC_HOST}/redfish/v1"

        self.session = aiohttp.ClientSession(
            auth=aiohttp.BasicAuth(
                settings.IDRAC_USERNAME,
                settings.IDRAC_PASSWORD,
            ),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def get(self, endpoint: str) -> dict:
        """Perform a GET request against the Redfish API."""

        url = f"{self.base_url}{endpoint}"
        logger.debug(
            "Redfish GET %s",
            url,
        )   

        async with self.session.get(url, ssl=False) as response:
            response.raise_for_status()
            return await response.json()

    async def get_system(self) -> dict:
        return await self.get("/Systems/System.Embedded.1")

    async def get_chassis(self) -> dict:
        return await self.get("/Chassis/System.Embedded.1")

    async def get_manager(self) -> dict:
        return await self.get("/Managers/iDRAC.Embedded.1")

    async def get_idrac_manager(self) -> dict:
        """Get the iDRAC Manager resource."""
        return await self.get("/Managers/iDRAC.Embedded.1")

    async def get_idrac_attributes(self) -> dict:
        """Get Dell OEM iDRAC attributes."""
        return await self.get(
            "/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/iDRAC.Embedded.1"
        )

    async def get_system_attributes(self) -> dict:
        """Get Dell OEM system attributes."""
        return await self.get(
            "/Managers/iDRAC.Embedded.1/Oem/Dell/DellAttributes/System.Embedded.1"
        )

    async def get_storage(self) -> dict:
        """Get the system storage collection."""
        return await self.get("/Systems/System.Embedded.1/Storage")

    async def get_storage_controller(self, controller_id: str) -> dict:
        """Get a storage controller."""
        return await self.get(
            f"/Systems/System.Embedded.1/Storage/{controller_id}"
        )

    async def get_storage_volumes(self, controller_id: str) -> dict:
        """Get the volume collection for a storage controller."""
        return await self.get(
            f"/Systems/System.Embedded.1/Storage/{controller_id}/Volumes"
        )

    async def get_storage_volume(self, controller_id: str, volume_id: str) -> dict:
        """Get a virtual disk."""
        return await self.get(
            f"/Systems/System.Embedded.1/Storage/{controller_id}/Volumes/{volume_id}"
        )

    async def get_storage_drive(self, controller_id: str, drive_id: str) -> dict:
        """Get a physical drive."""
        return await self.get(
            f"/Systems/System.Embedded.1/Storage/{controller_id}/Drives/{drive_id}"
        )


    async def get_power(self) -> dict:
        """Get chassis power supplies and power consumption."""
        return await self.get("/Chassis/System.Embedded.1/Power")

    async def get_thermal(self) -> dict:
        """Get chassis thermal information."""
        return await self.get("/Chassis/System.Embedded.1/Thermal")

    async def get_log_entries(self, log_service: str, skip: int = 0) -> dict:
        """
        Get one page of an iDRAC log service's entries, e.g. "Sel"
        (System Event Log) or "Lclog" (Lifecycle log).
        """
        endpoint = f"/Managers/iDRAC.Embedded.1/LogServices/{log_service}/Entries"

        if skip:
            endpoint += f"?$skip={skip}"

        return await self.get(endpoint)

    async def close(self):
        await self.session.close()