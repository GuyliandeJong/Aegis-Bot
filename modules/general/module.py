"""Help, maintenance mode, the weekly summary and the heartbeat."""

from core.modules import Module


MODULE = Module(
    name="general",
    description="Help, maintenance mode, the weekly summary and the heartbeat.",
    monitors=(
        "modules.general.weekly_summary_monitor:WeeklySummaryMonitor",
        "modules.general.heartbeat_monitor:HeartbeatMonitor",
        "modules.general.maintenance_monitor:MaintenanceMonitor",
    ),
    always_on=True,
)
