import discord
from discord import app_commands
from discord.ext import commands

from services.network_service import NetworkService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.formatting import format_bytes
from utils.logger import logger


class Network(commands.Cog):
    """
    Discord command for displaying network information.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="network",
        description="View network information.",
    )
    async def network(self, interaction: discord.Interaction):
        """
        Retrieve and display the current network status.
        """

        # Defer the response because retrieving network information
        # requires an asynchronous service request.
        await interaction.response.defer()

        try:
            # Retrieve the current network status.
            status = await NetworkService().get_network_status()

            network = status.network

            # Build the network information embed using the
            # centralized Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=f"{theme.NETWORK_EMOJI} Network",
                color=theme.INFO_COLOR,
                fields=[
                    EmbedField(
                        "Hostname",
                        status.hostname,
                        True,
                    ),
                    EmbedField(
                        "Interface",
                        network.interface,
                        True,
                    ),
                    EmbedField(
                        "IPv4",
                        network.ipv4,
                        True,
                    ),
                    EmbedField(
                        "MAC Address",
                        network.mac_address,
                        True,
                    ),
                    EmbedField(
                        "Status",
                        (
                            f"{theme.SUCCESS_EMOJI} Up"
                            if network.is_up
                            else f"{theme.ERROR_EMOJI} Down"
                        ),
                        True,
                    ),
                    EmbedField(
                        "Speed",
                        f"{network.speed} Mbps",
                        True,
                    ),
                    EmbedField(
                        "Upload",
                        format_bytes(network.bytes_sent),
                        True,
                    ),
                    EmbedField(
                        "Download",
                        format_bytes(network.bytes_received),
                        True,
                    ),
                ],
            )

            # Send the completed network information embed.
            await interaction.followup.send(embed=embed)

            logger.info(
                "Network status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # keeping the Discord response clean.
            logger.exception(
                "Failed to retrieve network status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} Network Error",
                description=(
                    "Unable to retrieve network information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Network(bot))