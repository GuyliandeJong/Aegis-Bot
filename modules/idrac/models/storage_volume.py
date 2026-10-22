from dataclasses import dataclass


@dataclass
class StorageVolume:
    name: str
    raid_type: str
    capacity: str

    health: str
    raid_status: str