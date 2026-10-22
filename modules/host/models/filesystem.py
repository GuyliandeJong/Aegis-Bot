from dataclasses import dataclass


@dataclass
class Filesystem:
    device: str
    mountpoint: str
    filesystem: str

    total: str
    used: str
    free: str

    percent_used: int