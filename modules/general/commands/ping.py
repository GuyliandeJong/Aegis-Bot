import discord
from discord import app_commands
from discord.ext import commands

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger


class Ping(commands.Cog):
    """
    Discord command for displaying the bot's current latency
    and connection status.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="ping",
        description="Displays the bot latency.",
    )
    async def ping(self, interaction: discord.Interaction):
        """
        Display the bot's current Discord gateway latency.
        """

        # Discord.py exposes the gateway latency as seconds.
        # Convert it to milliseconds for easier reading.
        latency = round(self.bot.latency * 1000)

        # Build the ping response using the centralized
        # Aegis-Bot theme.
        embed = create_embed(
            interaction=interaction,
            title="🏓 Pong!",
            description=(
                "The bot is online and responding."
            ),
            color=theme.SUCCESS_COLOR,
            fields=[
                EmbedField(
                    name="📡 Latency",
                    value=f"`{latency} ms`",
                ),
                EmbedField(
                    name="🤖 Status",
                    value=(
                        f"{theme.SUCCESS_EMOJI} Online"
                    ),
                ),
            ],
        )

        # Send the response directly because no asynchronous
        # external service call is required.
        await interaction.response.send_message(
            embed=embed
        )

        logger.info(
            "Ping requested by %s: %s ms",
            interaction.user,
            latency,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Ping(bot))