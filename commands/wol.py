import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from services.gptwol_service import GPTWOLService
from utils.logger import logger


class WOL(commands.Cog):
    """
    Discord commands for checking and waking the configured computer
    through GPTWOL.

    Aegis-Bot does not send Wake-on-LAN packets itself.
    It delegates the operation to the existing GPTWOL installation.

    Available commands:
        /wol status
        /wol wake
    """

    def __init__(self, bot):
        self.bot = bot
        self.service = GPTWOLService()

    @app_commands.command(
        name="wol",
        description="Check or wake the configured computer through GPTWOL.",
    )
    @app_commands.describe(action="Action to perform.")
    @app_commands.choices(
        action=[
            app_commands.Choice(
                name="Status",
                value="status",
            ),
            app_commands.Choice(
                name="Wake",
                value="wake",
            ),
        ]
    )
    async def wol(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ):
        if action.value == "status":
            await self._status(interaction)
            return

        if action.value == "wake":
            await self._wake(interaction)
            return

    async def _status(self, interaction: discord.Interaction):
        """Check and display the current computer status."""

        await interaction.response.defer()

        try:
            status = await self.service.check_status(
                ip_address=settings.GPTWOL_TARGET_IP,
                test_type=settings.GPTWOL_TARGET_TEST_TYPE,
            )

        except Exception as ex:
            logger.exception(
                "[WOL] Failed to check GPTWOL status: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to retrieve the computer status from GPTWOL."
            )
            return

        if status == "awake":
            status_text = "Awake"
        else:
            status_text = "Asleep"

        embed = discord.Embed(
            title="GPTWOL Status",
            description=(
                f"**Computer**\n"
                f"{settings.GPTWOL_TARGET_NAME}\n\n"
                f"**IP Address**\n"
                f"`{settings.GPTWOL_TARGET_IP}`\n\n"
                f"**Status**\n"
                f"{status_text}"
            ),
            color=discord.Color.blurple(),
        )

        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed)

        logger.info(
            "[WOL] Status requested by %s: %s",
            interaction.user,
            status,
        )

    async def _wake(self, interaction: discord.Interaction):
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
            embed = discord.Embed(
                title="Computer Already Awake",
                description=(
                    f"**Computer**\n"
                    f"{settings.GPTWOL_TARGET_NAME}\n\n"
                    f"The computer is already awake."
                ),
                color=discord.Color.blurple(),
            )

            embed.timestamp = discord.utils.utcnow()

            await interaction.followup.send(embed=embed)

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

        embed = discord.Embed(
            title="Wake-on-LAN Sent",
            description=(
                f"**Computer**\n"
                f"{settings.GPTWOL_TARGET_NAME}\n\n"
                f"**IP Address**\n"
                f"`{settings.GPTWOL_TARGET_IP}`\n\n"
                "Wake-on-LAN request sent through GPTWOL."
            ),
            color=discord.Color.blurple(),
        )

        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed)

        logger.info(
            "[WOL] Wake requested by %s for %s.",
            interaction.user,
            settings.GPTWOL_TARGET_NAME,
        )


async def setup(bot):
    await bot.add_cog(WOL(bot))