from dataclasses import dataclass

from models.storage_controller import StorageController
from models.storage_drive import StorageDrive
from models.storage_volume import StorageVolume


@dataclass
class StorageStatus:
    controller: StorageController
    volumes: list[StorageVolume]
    drives: list[StorageDrive]