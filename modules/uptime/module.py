"""Public URLs answering, and their TLS certificates expiring."""

from core.modules import Module


MODULE = Module(
    name="uptime",
    description="Public URLs answering, and their TLS certificates expiring.",
    required=(
        "UPTIME_URLS",
    ),
    monitors=(
        "modules.uptime.uptime_monitor:UptimeMonitor",
    ),
)
