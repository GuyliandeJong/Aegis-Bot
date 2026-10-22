from dataclasses import dataclass


@dataclass
class StorageController:
    model: str
    firmware: str
    health: str