from models.storage_controller import StorageController
from models.storage_drive import StorageDrive
from models.storage_status import StorageStatus
from models.storage_volume import StorageVolume
from redfish.client import RedfishClient
from utils.formatting import format_capacity


class StorageService:
    def __init__(self, client: RedfishClient):
        self.client = client

    async def get_storage_status(self) -> StorageStatus:
        storage = await self.client.get_storage()

        controller_id = None

        for member in storage.get("Members", []):
            odata_id = member["@odata.id"]

            if "/Storage/RAID." in odata_id:
                controller_id = odata_id.split("/")[-1]
                break

        if controller_id is None:
            raise RuntimeError("No RAID controller found.")

        controller = await self.client.get_storage_controller(controller_id)

        storage_controller = controller["StorageControllers"][0]

        controller_status = StorageController(
            model=storage_controller["Model"],
            firmware=storage_controller["FirmwareVersion"],
            health=storage_controller["Status"]["Health"],
        )

        volume_collection = await self.client.get_storage_volumes(controller_id)

        volumes = []

        for member in volume_collection.get("Members", []):
            volume_id = member["@odata.id"].split("/")[-1]

            volume = await self.client.get_storage_volume(
                controller_id,
                volume_id,
            )

            volumes.append(
                StorageVolume(
                    name=volume["Name"],
                    raid_type=volume["RAIDType"],
                    capacity=format_capacity(
                        volume["CapacityBytes"]
                    ),
                    health=volume["Status"]["Health"],
                    raid_status=volume["Oem"]["Dell"]["DellVolume"]["RaidStatus"],
                )
            )

        drives = []

        for member in controller.get("Drives", []):
            drive_id = member["@odata.id"].split("/")[-1]

            drive = await self.client.get_storage_drive(
                controller_id,
                drive_id,
            )

            dell_drive = drive["Oem"]["Dell"]["DellPhysicalDisk"]

            drives.append(
                StorageDrive(
                    slot=dell_drive["Slot"],
                    model=drive["Model"],
                    capacity=format_capacity(
                        drive["CapacityBytes"]
                    ),
                    media_type=drive["MediaType"],
                    protocol=drive["Protocol"],
                    rpm=drive.get("RotationSpeedRPM"),
                    health=drive["Status"]["Health"],
                    raid_status=dell_drive["RaidStatus"],
                    predictive_failure=drive["FailurePredicted"],
                    media_life_left=drive.get("PredictedMediaLifeLeftPercent"),
                )
            )

        return StorageStatus(
            controller=controller_status,
            volumes=volumes,
            drives=drives,
        )