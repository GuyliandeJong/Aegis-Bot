import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from modules.wol.gptwol_service import GPTWOLService
from utils.logger import logger


class WOL(commands.Cog):
    """
    Discord command for waking the configured computer through GPTWOL.

    Aegis-Bot does not send Wake-on-LAN packets itself.
    It delegates the operation to the existing GPTWOL installation.

    Available command:
        /wol
    """

    def __init__(self, bot):
        self.bot = bot
        self.service = GPTWOLService()

    @staticmethod
    def _embed(title: str, message: str) -> discord.Embed:
        """Build the standard /wol response."""
        embed = discord.Embed(
            title=title,
            description=(
                f"**Computer**\n"
                f"{settings.GPTWOL_TARGET_NAME}\n\n"
                f"{message}"
            ),
            color=discord.Color.blurple(),
        )

        embed.timestamp = discord.utils.utcnow()

        return embed

    # Waking a computer is restricted to server administrators by
    # default. This can be changed per user or role in Discord under
    # Server Settings > Integrations.
    @app_commands.command(
        name="wol",
        description="Wake the configured computer, or report that it is already awake.",
    )
    @app_commands.default_permissions(administrator=True)
    async def wol(self, interaction: discord.Interaction):
        """
        Wake the computer if it is currently asleep.

        GPTWOL's /wol_or_sol_send endpoint is a toggle operation.
        Therefore we must check the current state first so that an
        already-awake computer is never accidentally put to sleep.
        """

        await interaction.response.defer()

        # First determine the current state.
        try:
            status = await self.service.check_status(
                ip_address=settings.GPTWOL_TARGET_IP,
                test_type=settings.GPTWOL_TARGET_TEST_TYPE,
            )

        except Exception as ex:
            logger.exception(
                "[WOL] Failed to check computer status before wake: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to determine the computer's current status."
            )
            return

        # Do not call GPTWOL's toggle endpoint when the PC is already awake.
        if status == "awake":
            await interaction.followup.send(
                embed=self._embed(
                    "Computer Already Awake",
                    "The computer is already awake.",
                )
            )

            logger.info(
                "[WOL] Wake requested by %s, but %s is already awake.",
                interaction.user,
                settings.GPTWOL_TARGET_NAME,
            )
            return

        # The computer is asleep, so GPTWOL's toggle operation will
        # perform the Wake-on-LAN action.
        try:
            await self.service.send_wol_or_sol(
                mac_address=settings.GPTWOL_TARGET_MAC,
            )

        except Exception as ex:
            logger.exception(
                "[WOL] Failed to wake computer through GPTWOL: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to send the Wake-on-LAN request through GPTWOL."
            )
            return

        await interaction.followup.send(
            embed=self._embed(
                "Wake-on-LAN Sent",
                "The computer was asleep. A wake request has been sent.",
            )
        )

        logger.info(
            "[WOL] Wake requested by %s for %s.",
            interaction.user,
            settings.GPTWOL_TARGET_NAME,
        )


async def setup(bot):
    await bot.add_cog(WOL(bot))
