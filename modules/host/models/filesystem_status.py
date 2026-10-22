from dataclasses import dataclass

from modules.host.models.filesystem import Filesystem


@dataclass
class FilesystemStatus:
    filesystems: list[Filesystem]