import discord
from discord import app_commands
from discord.ext import commands

from modules.idrac.idrac_service import IdracService

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger


class Idrac(commands.Cog):
    """
    Discord command for displaying iDRAC management information.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="idrac",
        description="Displays iDRAC information.",
    )
    async def idrac(self, interaction: discord.Interaction):
        """
        Retrieve and display the current iDRAC status.
        """

        # Defer the response because the iDRAC information is
        # retrieved asynchronously.
        await interaction.response.defer(ephemeral=True)

        # Create the iDRAC service used to retrieve the information.
        service = IdracService()

        try:
            # Retrieve the current iDRAC status.
            status = await service.get_status()

            # Build the iDRAC information using the shared embed
            # structure and Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=f"🛠️ iDRAC",
                description=(
                    "Live iDRAC management information"
                ),
                color=theme.PRIMARY_COLOR,
                fields=[
                    EmbedField(
                        "🟢 Health",
                        status.health,
                    ),
                    EmbedField(
                        "🏷️ Hostname",
                        status.hostname,
                    ),
                    EmbedField(
                        "🔧 Firmware",
                        status.firmware_version,
                    ),
                    EmbedField(
                        "🌐 IP Address",
                        status.ip_address,
                    ),
                    EmbedField(
                        "📡 MAC Address",
                        status.mac_address,
                        inline=False,
                    ),
                ],
            )

            # Send the completed iDRAC information.
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )

            logger.info(
                "iDRAC status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception and return a clean
            # error message to Discord.
            logger.exception(
                "Failed to retrieve iDRAC status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} iDRAC Error",
                description=(
                    "Unable to retrieve iDRAC information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )

        finally:
            # Always close the iDRAC service, including when
            # retrieving the status fails.
            await service.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(Idrac(bot))