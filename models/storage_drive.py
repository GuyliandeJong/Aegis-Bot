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