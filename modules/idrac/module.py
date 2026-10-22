"""Dell server hardware through the iDRAC Redfish API."""

from core.modules import Module


MODULE = Module(
    name="idrac",
    description="Dell server hardware through the iDRAC Redfish API.",
    required=(
        "IDRAC_HOST",
        "IDRAC_USERNAME",
        "IDRAC_PASSWORD",
    ),
    monitors=(
        "modules.idrac.temperature_monitor:TemperatureMonitor",
        "modules.idrac.storage_monitor:StorageMonitor",
        "modules.idrac.hardware_monitor:HardwareMonitor",
        "modules.idrac.event_log_monitor:EventLogMonitor",
    ),
    startup_checks="modules.idrac.checks:checks",
)
