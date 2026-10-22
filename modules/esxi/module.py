"""VMware ESXi datastores, virtual machines and snapshots."""

from core.modules import Module


MODULE = Module(
    name="esxi",
    description="VMware ESXi datastores, virtual machines and snapshots.",
    required=(
        "ESXI_HOST",
        "ESXI_USERNAME",
        "ESXI_PASSWORD",
    ),
    monitors=(
        "modules.esxi.esxi_monitor:EsxiMonitor",
    ),
)
