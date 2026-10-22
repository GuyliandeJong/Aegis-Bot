import discord
from discord import app_commands
from discord.ext import commands

from modules.idrac.temperature_service import TemperatureService

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger
from utils.status import (
    health_icon,
    health_text,
    temperature_status,
)


class Temperature(commands.Cog):
    """
    Discord command for displaying important system temperatures.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="temperature",
        description="View important system temperatures.",
    )
    async def temperature(self, interaction: discord.Interaction):
        """
        Retrieve and display the current system temperatures.
        """

        # Defer the response because temperature information
        # is retrieved asynchronously from the server.
        await interaction.response.defer()

        try:
            # Retrieve the current temperature sensor readings.
            service = TemperatureService()
            status = await service.get_temperature_status()

            fields = []

            # Add every supported temperature sensor to the embed.
            for sensor in status.temperatures:
                # Determine the Aegis-Bot temperature status using
                # the configured warning and critical thresholds.
                current_status = temperature_status(
                    sensor.reading
                )

                fields.append(
                    EmbedField(
                        f"{theme.TEMPERATURE_EMOJI} {sensor.name}",
                        (
                            f"Temperature\n"
                            f"{sensor.reading:.1f}°C\n\n"
                            f"Status\n"
                            f"{health_icon(current_status)} "
                            f"{health_text(current_status)}"
                        ),
                        False,
                    )
                )

            # Provide a clear response when no supported sensors
            # are available on the system.
            if not fields:
                fields.append(
                    EmbedField(
                        "No Sensors",
                        "No supported temperature sensors were found.",
                        False,
                    )
                )

            # Build the temperature embed using the centralized
            # Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=(
                    f"{theme.TEMPERATURE_EMOJI} "
                    f"Temperatures"
                ),
                color=theme.PRIMARY_COLOR,
                fields=fields,
            )

            # Send the completed temperature information.
            await interaction.followup.send(
                embed=embed
            )

            logger.info(
                "Temperature status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # returning a clean error message to Discord.
            logger.exception(
                "Failed to retrieve temperature status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} Temperature Error",
                description=(
                    "Unable to retrieve temperature information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Temperature(bot))