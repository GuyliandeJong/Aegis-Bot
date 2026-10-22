import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config.settings import settings
from modules.esxi.esxi_monitor import EsxiMonitor
from modules.idrac.event_log_monitor import EventLogMonitor
from modules.host.filesystem_monitor import FilesystemMonitor
from modules.idrac.hardware_monitor import HardwareMonitor
from modules.general.heartbeat_monitor import HeartbeatMonitor
from modules.jellyfin.new_content_monitor import JellyfinNewContentMonitor
from modules.minecraft.minecraft_monitor import MinecraftMonitor
from core.monitor_manager import MonitorManager
from modules.torrents.seed_cleanup_monitor import SeedCleanupMonitor
from modules.wan.wan_monitor import WanMonitor
from modules.torrents.watchlist_monitor import WatchlistMonitor
from modules.general.weekly_summary_monitor import WeeklySummaryMonitor
from modules.idrac.temperature_monitor import TemperatureMonitor
from modules.host.cpu_monitor import CPUMonitor
from modules.containers.container_monitor import ContainerMonitor
from modules.host.memory_monitor import MemoryMonitor
from modules.idrac.storage_monitor import StorageMonitor
from modules.jellyfin.subtitle_monitor import SubtitleMonitor
from modules.torrents.torrent_monitor import TorrentMonitor
from modules.torrents.torrent_health_monitor import TorrentHealthMonitor
from modules.backup.backup_monitor import BackupMonitor
from modules.general.maintenance_monitor import MaintenanceMonitor
from modules.torrents.vpn_monitor import VpnMonitor
from modules.uptime.uptime_monitor import UptimeMonitor
from core import startup_check
from modules.torrents.qbittorrent_service import QBittorrentService
from modules.torrents.torrent_action_tracker import (
    install_qbittorrent_action_tracking,
)
from utils import open_messages
from utils.logger import logger


# Wrap qBittorrent pause/resume methods so the background monitor can
# identify actions initiated through Discord commands and buttons.
install_qbittorrent_action_tracking()

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = discord.Object(id=int(os.getenv("GUILD_ID")))

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)

monitor_manager = MonitorManager(bot)
monitor_task = None


async def load_extensions():
    """Load all Discord command extensions from the commands directory."""
    for path in sorted(Path("modules").glob("*/commands/*.py")):
        if path.name != "__init__.py":
            filename = path.name
            module = ".".join(path.with_suffix("").parts)

            try:
                await bot.load_extension(module)
                logger.info("Loaded %s", filename)
            except Exception:
                logger.exception("Failed to load %s", filename)


async def startup_tasks():
    """Expire leftover buttons and check the services, once."""
    try:
        await startup_check.run(monitor_manager.notification_service)
    except Exception:
        logger.exception("Startup service check failed")

    try:
        await open_messages.expire_all(bot)
    except Exception:
        logger.exception("Expiring old messages failed")


@bot.event
async def on_ready():
    """Runs when the bot successfully connects to Discord."""
    global monitor_task

    logger.info("----------------------------------------")
    logger.info("Logged in as: %s", bot.user)
    logger.info("Bot ID: %s", bot.user.id)
    logger.info("----------------------------------------")

    bot.tree.copy_global_to(guild=GUILD_ID)

    torrent_command = bot.tree.get_command("torrent")

    if torrent_command is not None:
        logger.info("Inspecting /torrent command registration:")

        for subcommand in torrent_command.commands:
            logger.info("  /torrent %s", subcommand.name)

            if subcommand.name == "info":
                for parameter in subcommand.parameters:
                    logger.info(
                        "    option=%s type=%s autocomplete=%s",
                        parameter.name,
                        parameter.type,
                        parameter.autocomplete,
                    )

    logger.info("Commands currently in the command tree:")

    for command in bot.tree.get_commands():
        logger.info("  /%s", command.name)

    synced = await bot.tree.sync(guild=GUILD_ID)

    logger.info("Registered guild slash commands:")
    for command in synced:
        logger.info("  /%s", command.name)

    logger.info(
        "Synced %s guild slash command(s)",
        len(synced),
    )

    # Start the monitoring framework only once. Discord may call on_ready
    # again after a reconnect.
    if monitor_task is None:
        # Check the services and mark buttons from before the restart
        # as expired. Runs alongside the monitors: editing many old
        # messages can take a while because of Discord's rate limits.
        asyncio.create_task(startup_tasks())

        monitor_manager.register(
            FilesystemMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            WanMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            TemperatureMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            CPUMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            MemoryMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            StorageMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            HardwareMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            EventLogMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            TorrentMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            TorrentHealthMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            JellyfinNewContentMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            WatchlistMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            WeeklySummaryMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            SeedCleanupMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            HeartbeatMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            EsxiMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            MinecraftMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            SubtitleMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            ContainerMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            BackupMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            MaintenanceMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            VpnMonitor(monitor_manager.notification_service)
        )
        monitor_manager.register(
            UptimeMonitor(monitor_manager.notification_service)
        )

        monitor_task = asyncio.create_task(monitor_manager.run())
        logger.info("Monitoring framework started")


async def main():
    """Start Aegis-Bot."""
    # Older copies of config/settings.py do not track missing values.
    missing = getattr(settings, "MISSING", None)

    if missing:
        logger.warning(
            "Missing required .env values: %s. "
            "Features that depend on them will not work.",
            ", ".join(missing),
        )

    try:
        async with bot:
            await load_extensions()
            await bot.start(TOKEN)
    finally:
        await QBittorrentService.shutdown()


if __name__ == "__main__":
    asyncio.run(main())