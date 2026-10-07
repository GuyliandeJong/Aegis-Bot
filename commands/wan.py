import discord
from discord import app_commands
from discord.ext import commands

from services.wan_service import WanService

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger


class Wan(commands.Cog):
    """
    Discord command for retrieving WAN information.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = WanService()

    @app_commands.command(
        name="wan",
        description="Show the server's current public WAN IP.",
    )
    @app_commands.default_permissions(administrator=True)
    async def wan(self, interaction: discord.Interaction):
        """
        Retrieve and display the current public WAN IP.
        """

        # Defer the response because retrieving the WAN IP
        # requires an external HTTP request.
        await interaction.response.defer(ephemeral=True)

        try:
            # Retrieve the current public WAN IP.
            ip_address = await self.service.get_ip()

            # Build the response using the shared Aegis-Bot
            # embed layout and visual theme.
            embed = create_embed(
                interaction=interaction,
                title=f"{theme.NETWORK_EMOJI} WAN Information",
                description="Current public WAN connection information.",
                color=theme.INFO_COLOR,
                fields=[
                    EmbedField(
                        "🌐 Public IP",
                        f"`{ip_address}`",
                        inline=False,
                    ),
                    EmbedField(
                        "Status",
                        f"{theme.SUCCESS_EMOJI} Online",
                        inline=True,
                    ),
                ],
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                "WAN IP requested by %s: %s",
                interaction.user,
                ip_address,
            )

        except Exception:
            # Log the complete exception internally while
            # returning a clean message to Discord.
            logger.exception(
                "Failed to retrieve WAN IP"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} WAN Error",
                description=(
                    "Unable to retrieve the current public WAN IP."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Wan(bot))