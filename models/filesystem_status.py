from dataclasses import dataclass

from models.filesystem import Filesystem


@dataclass
class FilesystemStatus:
    filesystems: list[Filesystem]