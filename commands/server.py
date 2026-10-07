import discord
from discord import app_commands
from discord.ext import commands

from services.hardware_service import HardwareService
from services.server_service import ServerService
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils import theme
from utils.logger import logger


class Server(commands.Cog):
    """
    Discord command for displaying the current server status.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="server",
        description="Displays the current server status.",
    )
    @app_commands.default_permissions(administrator=True)
    async def server(self, interaction: discord.Interaction):
        """
        Retrieve and display live server information from iDRAC.
        """

        # Defer the response because the iDRAC requests can take
        # longer than Discord's three-second interaction limit.
        await interaction.response.defer(ephemeral=True)

        # Create the service used to retrieve server information.
        service = ServerService()

        try:
            # Retrieve the current server status.
            status = await service.get_status()

            # Power supplies, fans and power draw are extra: /server
            # still answers when iDRAC does not report them.
            try:
                hardware = await HardwareService().get_hardware_health()
            except Exception as ex:
                logger.warning("Hardware health unavailable: %s", ex)
                hardware = None

            # Build the server information embed using the
            # centralized Aegis-Bot theme.
            embed = create_embed(
                interaction=interaction,
                title=(
                    f"{theme.SERVER_EMOJI} "
                    f"{status.manufacturer} {status.model}"
                ),
                description="Live server information from iDRAC",
                color=theme.PRIMARY_COLOR,
                fields=[
                    EmbedField(
                        f"{theme.SUCCESS_EMOJI} Overall Health",
                        status.health,
                        inline=False,
                    ),
                    EmbedField(
                        "⚡ Power State",
                        status.power_state,
                        inline=False,
                    ),
                    EmbedField(
                        "🧠 CPU",
                        status.cpu_model,
                        inline=False,
                    ),
                    EmbedField(
                        f"{theme.STORAGE_EMOJI} Memory",
                        f"{status.total_memory_gb} GiB",
                        inline=False,
                    ),
                    EmbedField(
                        "💻 Operating System",
                        status.operating_system,
                        inline=False,
                    ),
                    EmbedField(
                        "📦 Version",
                        status.operating_system_version,
                        inline=False,
                    ),
                    EmbedField(
                        "🔧 BIOS",
                        status.bios_version,
                        inline=False,
                    ),
                    EmbedField(
                        "🏷️ Service Tag",
                        status.service_tag,
                        inline=False,
                    ),
                ],
            )

            if hardware is not None:
                embed.add_field(
                    name="🔌 Power Supplies and Fans",
                    value=hardware_summary(hardware),
                    inline=False,
                )

            logger.info(
                "Server status requested by %s",
                interaction.user,
            )

        except Exception:
            # Log the complete exception for troubleshooting while
            # returning a clean error message to Discord.
            logger.exception(
                "Failed to retrieve server status"
            )

            embed = create_embed(
                interaction=interaction,
                title=(
                    f"{theme.ERROR_EMOJI} Connection Failed"
                ),
                description=(
                    "Unable to retrieve server information."
                ),
                color=theme.ERROR_COLOR,
            )

        finally:
            # Always close the iDRAC service connection, even when
            # retrieving the server status fails.
            await service.close()

        # Send the completed embed to Discord.
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
        )


def hardware_summary(hardware: dict) -> str:
    """"2 power supplies OK · 6 fans OK · drawing 182 W"."""
    parts = []

    for kind, plural in (("Power supply", "power supplies"), ("Fan", "fans")):
        members = [
            component
            for component in hardware["components"].values()
            if component["kind"] == kind
        ]

        if not members:
            continue

        bad = [m for m in members if m["health"].lower() != "ok"]

        parts.append(
            f"{len(members)} {plural} OK"
            if not bad
            else f"{len(bad)} of {len(members)} {plural} not OK"
        )

    if hardware.get("power_watts") is not None:
        parts.append(f"drawing {hardware['power_watts']:.0f} W")

    return " · ".join(parts) or "Not reported by iDRAC"


async def setup(bot: commands.Bot):
    await bot.add_cog(Server(bot))