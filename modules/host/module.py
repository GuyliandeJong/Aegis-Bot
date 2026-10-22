"""CPU, memory, disks and network of the machine the bot runs on.

Disks are read through the read-only host mount at /host (see
docker-compose.example.yml); without it only the container's own disks
are seen."""

from core.modules import Module


MODULE = Module(
    name="host",
    description="CPU, memory, disks and network of the machine the bot runs on.",
    monitors=(
        "modules.host.filesystem_monitor:FilesystemMonitor",
        "modules.host.cpu_monitor:CPUMonitor",
        "modules.host.memory_monitor:MemoryMonitor",
    ),
)
