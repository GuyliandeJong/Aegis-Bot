"""Internet connectivity, public IP changes and restarts after an outage."""

from core.modules import Module


MODULE = Module(
    name="wan",
    description="Internet connectivity, public IP changes and restarts after an outage.",
    monitors=(
        "modules.wan.wan_monitor:WanMonitor",
    ),
)
