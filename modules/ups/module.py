"""UPS power: outages, low battery and a battery that needs replacing, through NUT."""

from core.modules import Module


MODULE = Module(
    name="ups",
    description="UPS power: outages, low battery and a battery that needs replacing, through NUT.",
    required=(
        "NUT_HOST",
    ),
    monitors=(
        "modules.ups.ups_monitor:UpsMonitor",
    ),
)
