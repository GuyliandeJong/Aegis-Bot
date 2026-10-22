"""Minecraft Java server status, joins and down alerts."""

from core.modules import Module


MODULE = Module(
    name="minecraft",
    description="Minecraft Java server status, joins and down alerts.",
    required=(
        "MINECRAFT_SERVERS",
    ),
    monitors=(
        "modules.minecraft.minecraft_monitor:MinecraftMonitor",
    ),
)
