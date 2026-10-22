import discord
from discord import app_commands
from discord.ext import commands

from modules.host.memory_service import MemoryService

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.formatting import usage_bar
from utils.logger import logger
from utils.status import usage_icon


class Memory(commands.Cog):
    """
    Discord command for displaying system memory usage.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="memory",
        description="View system memory usage.",
    )
    async def memory(self, interaction: discord.Interaction):
        """
        Retrieve and display the current system memory status.
        """

        # Defer the response because memory information is retrieved
        # asynchronously from the server.
        await interaction.response.defer()

        try:
            # Retrieve the current memory information.
            service = MemoryService()
            status = await service.get_memory_status()

            memory = status.memory

            # Build the memory information as a single summary field.
            fields = [
                EmbedField(
                    "📊 Summary",
                    (
                        f"Usage\n"
                        f"{usage_bar(round(memory.percent_used))}\n"
                        f"{memory.percent_used:.1f}%\n\n"
                        f"Used\n"
                        f"{memory.used}\n\n"
                        f"Available\n"
                        f"{memory.available}\n\n"
                        f"Total\n"
                        f"{memory.total}\n\n"
                        f"Cached\n"
                        f"{memory.cached}\n\n"
                        f"Swap\n"
                        f"{usage_icon(round(memory.swap_percent))} "
                        f"{memory.swap_used} / {memory.swap_total} "
                        f"({memory.swap_percent:.1f}%)"
                    ),
                    False,
                )
            ]

            # Build the embed using the centralized Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=f"{theme.MEMORY_EMOJI} Memory",
                color=theme.PRIMARY_COLOR,
                fields=fields,
            )

            # Send the completed memory information to Discord.
            await interaction.followup.send(
                embed=embed
            )

            logger.info(
                "Memory status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # returning a clean error message to Discord.
            logger.exception(
                "Failed to retrieve memory status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} Memory Error",
                description=(
                    "Unable to retrieve memory information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Memory(bot))