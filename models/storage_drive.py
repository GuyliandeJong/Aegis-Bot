from dataclasses import dataclass


@dataclass
class StorageDrive:
    slot: int
    model: str
    capacity: str
    media_type: str
    protocol: str
    rpm: int | None

    health: str
    raid_status: str

    predictive_failure: bool

    # Percent of an SSD's rated write endurance left; None for hard
    # disks and when iDRAC does not report it.
    media_life_left: float | None = None