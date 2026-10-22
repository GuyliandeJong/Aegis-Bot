import discord
from discord import app_commands
from discord.ext import commands

from modules.idrac.redfish_client import RedfishClient

from modules.idrac.storage_service import StorageService

from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger
from utils.status import (
    health_icon,
    health_text,
    status_icon,
)


class Storage(commands.Cog):
    """
    Discord command for displaying storage controller,
    RAID volume, and physical disk health.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="storage",
        description="View storage controller and physical disk health.",
    )
    async def storage(self, interaction: discord.Interaction):
        """
        Retrieve and display the current storage health.
        """

        # Defer the response because retrieving storage information
        # requires an asynchronous Redfish request.
        await interaction.response.defer()

        # Create the Redfish client used by StorageService.
        client = RedfishClient()

        try:
            # Retrieve the current storage controller, RAID,
            # and physical drive information.
            service = StorageService(client)
            status = await service.get_storage_status()

            # Start with information about the storage controller.
            fields = [
                EmbedField(
                    f"{theme.SERVER_EMOJI} Controller",
                    (
                        f"**{status.controller.model}**\n\n"
                        f"Health\n"
                        f"{health_icon(status.controller.health)} "
                        f"{health_text(status.controller.health)}\n\n"
                        f"Firmware\n"
                        f"{status.controller.firmware}"
                    ),
                    False,
                )
            ]

            # Add every RAID volume reported by the controller.
            for volume in status.volumes:
                fields.append(
                    EmbedField(
                        "💽 RAID Volume",
                        (
                            f"**{volume.name}**\n\n"
                            f"Type\n"
                            f"{volume.raid_type}\n\n"
                            f"Capacity\n"
                            f"{volume.capacity}\n\n"
                            f"Health\n"
                            f"{health_icon(volume.health)} "
                            f"{health_text(volume.health)}\n\n"
                            f"Status\n"
                            f"{status_icon(volume.raid_status)} "
                            f"{volume.raid_status}"
                        ),
                        False,
                    )
                )

            # Sort physical drives by their slot number so that
            # they appear in a predictable order.
            for drive in sorted(
                status.drives,
                key=lambda d: d.slot,
            ):
                # Report rotational speed for HDDs and SSD for
                # drives where no RPM value is available.
                rpm = (
                    f"{drive.rpm:,} RPM"
                    if drive.rpm
                    else "SSD"
                )

                fields.append(
                    EmbedField(
                        f"{theme.STORAGE_EMOJI} Drive {drive.slot}",
                        (
                            f"**{drive.model}**\n\n"
                            f"Capacity\n"
                            f"{drive.capacity}\n\n"
                            f"Interface\n"
                            f"{drive.protocol} {drive.media_type}\n\n"
                            f"Speed\n"
                            f"{rpm}\n\n"
                            f"Health\n"
                            f"{health_icon(drive.health)} "
                            f"{health_text(drive.health)}\n\n"
                            f"Status\n"
                            f"{status_icon(drive.raid_status)} "
                            f"{drive.raid_status}"
                        ),
                        False,
                    )
                )

            # Build the storage embed using the centralized
            # Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=f"{theme.STORAGE_EMOJI} Storage",
                color=theme.PRIMARY_COLOR,
                fields=fields,
            )

            # Send the completed storage information.
            await interaction.followup.send(
                embed=embed
            )

            logger.info(
                "Storage status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # returning a clean error message to Discord.
            logger.exception(
                "Failed to retrieve storage status"
            )

            embed = create_embed(
                interaction=interaction,
                title=f"{theme.ERROR_EMOJI} Storage Error",
                description=(
                    "Unable to retrieve storage information."
                ),
                color=theme.ERROR_COLOR,
            )

            await interaction.followup.send(
                embed=embed
            )

        finally:
            # Always close the Redfish client, even when the
            # storage request fails.
            await client.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(Storage(bot))