import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from modules.host.filesystem_service import FilesystemService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.formatting import usage_bar
from utils.logger import logger


class Filesystems(commands.Cog):
    """
    Discord command for displaying mounted filesystems
    and their current disk usage.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="filesystems",
        description="View mounted filesystems and disk usage.",
    )
    async def filesystems(self, interaction: discord.Interaction):
        """
        Retrieve and display filesystem usage information.
        """

        # Defer the response because filesystem information is
        # retrieved asynchronously from the server.
        await interaction.response.defer()

        try:
            # Retrieve the current filesystem information.
            service = FilesystemService()
            status = await service.get_filesystems()

            # Count filesystems belonging to each health state.
            healthy = sum(
                1
                for filesystem in status.filesystems
                if filesystem.percent_used < settings.FILESYSTEM_WARNING
            )

            warning = sum(
                1
                for filesystem in status.filesystems
                if settings.FILESYSTEM_WARNING
                <= filesystem.percent_used
                < settings.FILESYSTEM_CRITICAL
            )

            critical = sum(
                1
                for filesystem in status.filesystems
                if filesystem.percent_used >= settings.FILESYSTEM_CRITICAL
            )

            # Start the embed with an overview of the filesystem
            # health across the entire server.
            fields = [
                EmbedField(
                    "📊 Summary",
                    (
                        f"Mounted: **{len(status.filesystems)}**\n"
                        f"{theme.SUCCESS_EMOJI} Healthy: **{healthy}**\n"
                        f"{theme.WARNING_EMOJI} Warning: **{warning}**\n"
                        f"{theme.ERROR_EMOJI} Critical: **{critical}**"
                    ),
                    False,
                )
            ]

            # Add detailed information for every mounted filesystem.
            for filesystem in status.filesystems:
                fields.append(
                    EmbedField(
                        f"📁 {filesystem.mountpoint}",
                        (
                            f"Device\n"
                            f"{filesystem.device}\n\n"
                            f"Filesystem\n"
                            f"{filesystem.filesystem}\n\n"
                            f"Usage\n"
                            f"{usage_bar(filesystem.percent_used)} "
                            f"{filesystem.percent_used}%\n\n"
                            f"Used\n"
                            f"{filesystem.used}\n\n"
                            f"Available\n"
                            f"{filesystem.free}\n\n"
                            f"Total\n"
                            f"{filesystem.total}"
                        ),
                        False,
                    )
                )

            # Build the final embed using the centralized
            # Aegis-Bot storage theme.
            embed = create_embed(
                interaction=interaction,
                title=f"{theme.STORAGE_EMOJI} Filesystems",
                color=theme.PRIMARY_COLOR,
                fields=fields,
            )

            # Send the completed filesystem information.
            await interaction.followup.send(
                embed=embed
            )

            logger.info(
                "Filesystem status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # returning a clean error message to Discord.
            logger.exception(
                "Failed to retrieve filesystem status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} Filesystem Error",
                description=(
                    "Unable to retrieve filesystem information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Filesystems(bot))