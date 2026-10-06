import discord
from discord import app_commands
from discord.ext import commands

from services.cpu_service import CPUService

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.formatting import usage_bar
from utils.logger import logger


class CPU(commands.Cog):
    """
    Discord command for displaying processor information
    and current CPU utilization.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="cpu",
        description="View processor usage and information.",
    )
    async def cpu(self, interaction: discord.Interaction):
        """
        Retrieve and display the current CPU status.
        """

        # Defer the response because CPU information is retrieved
        # asynchronously from the server.
        await interaction.response.defer()

        try:
            # Retrieve the current processor information and usage.
            service = CPUService()
            status = await service.get_cpu_status()

            cpu = status.cpu

            # Build the CPU information as a single summary field.
            fields = [
                EmbedField(
                    "📊 Summary",
                    (
                        f"Processor\n"
                        f"{cpu.processor}\n\n"
                        f"Usage\n"
                        f"{usage_bar(round(cpu.usage_percent))}\n"
                        f"{cpu.usage_percent:.1f}%\n\n"
                        f"Current Frequency\n"
                        f"{cpu.current_frequency:.2f} GHz\n\n"
                        f"Physical Cores\n"
                        f"{cpu.physical_cores}\n\n"
                        f"Logical Threads\n"
                        f"{cpu.logical_cores}\n\n"
                        f"Uptime\n"
                        f"{cpu.uptime}"
                    ),
                    False,
                )
            ]

            # Build the embed using the centralized Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=f"{theme.CPU_EMOJI} CPU",
                color=theme.PRIMARY_COLOR,
                fields=fields,
            )

            # Send the completed CPU information to Discord.
            await interaction.followup.send(
                embed=embed
            )

            logger.info(
                "CPU status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # returning a clean error message to Discord.
            logger.exception(
                "Failed to retrieve CPU status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} CPU Error",
                description=(
                    "Unable to retrieve CPU information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(CPU(bot))