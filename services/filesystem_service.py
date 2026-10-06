import psutil

from models.filesystem import Filesystem
from models.filesystem_status import FilesystemStatus
from utils.formatting import format_capacity


class FilesystemService:
    IMPORTANT_EXACT = {
        "/host",
        "/host/boot",
        "/app/data",
    }

    IMPORTANT_PREFIXES = (
        "/host/mnt/",
    )

    async def get_filesystems(self) -> FilesystemStatus:
        filesystems = []
        seen_devices = set()

        for partition in psutil.disk_partitions(all=True):
            mountpoint = partition.mountpoint

            if (
                mountpoint not in self.IMPORTANT_EXACT
                and not mountpoint.startswith(self.IMPORTANT_PREFIXES)
            ):
                continue

            try:
                usage = psutil.disk_usage(mountpoint)
            except (PermissionError, FileNotFoundError):
                continue

            display_mount = mountpoint

            if display_mount.startswith("/host"):
                display_mount = display_mount.removeprefix("/host")

                if display_mount == "":
                    display_mount = "/"

            # Skip duplicate devices
            if partition.device in seen_devices:
                continue

            seen_devices.add(partition.device)

            filesystems.append(
                Filesystem(
                    device=partition.device,
                    mountpoint=display_mount,
                    filesystem=partition.fstype or "Unknown",
                    total=format_capacity(usage.total),
                    used=format_capacity(usage.used),
                    free=format_capacity(usage.free),
                    percent_used=round(usage.percent),
                )
            )

        filesystems.sort(
            key=lambda filesystem: (
                filesystem.mountpoint != "/",
                filesystem.mountpoint,
            )
        )

        return FilesystemStatus(filesystems)