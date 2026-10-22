from dataclasses import dataclass

from modules.idrac.models.storage_controller import StorageController
from modules.idrac.models.storage_drive import StorageDrive
from modules.idrac.models.storage_volume import StorageVolume


@dataclass
class StorageStatus:
    controller: StorageController
    volumes: list[StorageVolume]
    drives: list[StorageDrive]