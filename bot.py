"""
Aegis-Bot: a Discord bot for watching and running a home server.

Every feature is a module in modules/ (see core/modules.py). At startup
the bot works out which modules run, loads their slash commands, runs
their setup, and starts their monitors once Discord is connected.
"""

import asyncio
import sys

import discord
from discord.ext import commands

from config.settings import settings
from core import modules, startup_check
from core.monitor_manager import MonitorManager
from utils import open_messages
from utils.logger import logger


intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)

monitor_manager = MonitorManager(bot)
monitor_task = None

# The modules that run in this process, worked out in main().
active: list[modules.Module] = []


async def load_extensions():
    """Load the slash commands of every running module."""
    for module in active:
        for extension in module.commands():
            try:
                await bot.load_extension(extension)
                logger.info("Loaded %s", extension)
            except Exception:
                logger.exception("Failed to load %s", extension)


def run_setups():
    """Each running module's one-time setup, before commands load."""
    for module in active:
        if module.setup:
            try:
                modules.resolve(module.setup)()
            except Exception:
                logger.exception("Setup of the %s module failed", module.name)


def start_monitors():
    """Register the monitors of every running module."""
    for module in active:
        for target in module.monitors:
            try:
                monitor_class = modules.resolve(target)
                monitor_manager.register(
                    monitor_class(monitor_manager.notification_service)
                )
            except Exception:
                logger.exception("Could not start %s", target)


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

    guild = discord.Object(id=settings.GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)

    logger.info(
        "Synced %s guild slash command(s): %s",
        len(synced),
        ", ".join(f"/{command.name}" for command in synced),
    )

    # Start the monitoring framework only once. Discord may call on_ready
    # again after a reconnect.
    if monitor_task is None:
        # Check the services and mark buttons from before the restart
        # as expired. Runs alongside the monitors: editing many old
        # messages can take a while because of Discord's rate limits.
        asyncio.create_task(startup_tasks())

        start_monitors()

        monitor_task = asyncio.create_task(monitor_manager.run())
        logger.info("Monitoring framework started")


async def shutdown():
    """Close what the running modules keep open."""
    for module in active:
        if module.shutdown:
            try:
                await modules.resolve(module.shutdown)()
            except Exception:
                logger.exception("Shutdown of the %s module failed", module.name)


async def main():
    """Start Aegis-Bot."""
    if settings.MISSING:
        logger.error(
            "Missing or invalid in .env: %s. Aegis-Bot cannot start "
            "without them; see .env.example.",
            ", ".join(settings.MISSING),
        )
        sys.exit(1)

    states = modules.statuses(modules.discover())
    modules.activate(states)
    modules.report(states)
    active.extend(state.module for state in states if state.enabled)

    run_setups()

    try:
        async with bot:
            await load_extensions()
            await bot.start(settings.DISCORD_TOKEN)
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
