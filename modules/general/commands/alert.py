"""
/alert test [level]: post a test alert to the alert channel.

Shows that the alert channel, the bot's permissions and the Fluxer
mirror work, and what maintenance mode does to a level. The alert goes
through NotificationService like every real one, so maintenance mode
and quiet hours hold it (except critical). The reply to the command is
private.
"""

import discord
from discord import app_commands
from discord.ext import commands

from core import maintenance, quiet_hours
from core.notification_service import NotificationService


LEVELS = [
    app_commands.Choice(name="info", value="info"),
    app_commands.Choice(name="warning", value="warning"),
    app_commands.Choice(name="critical", value="critical"),
    app_commands.Choice(name="recovered", value="recovered"),
    app_commands.Choice(name="success", value="success"),
]


class AlertCog(commands.Cog):
    """The /alert command group."""

    # Administrators only by default, changeable under Server Settings
    # > Integrations.
    alert_group = app_commands.Group(
        name="alert",
        description="Alert tools.",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.notifications = NotificationService(bot)

    @alert_group.command(
        name="test",
        description="Post a test alert to the alert channel.",
    )
    @app_commands.describe(level="The kind of alert to send (default: info).")
    @app_commands.choices(level=LEVELS)
    async def test(
        self,
        interaction: discord.Interaction,
        level: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        name = level.value if level else "info"
        send = getattr(self.notifications, name)

        await self.notifications.end_maintenance_if_expired()

        message = await send(
            title=f"Test Alert ({name})",
            description=(
                f"<@{interaction.user.id}> sent this with `/alert test`. "
                "Nothing is wrong."
            ),
        )

        if message is not None:
            channel = getattr(message, "channel", None)
            where = f"<#{channel.id}>" if channel is not None else "the alert channel"
            await interaction.followup.send(
                f"The {name} test alert was posted in {where}.",
                ephemeral=True,
            )
            return

        if maintenance.active():
            reason = (
                "Maintenance mode is on and mutes this level. Critical "
                "alerts still come through; see `/maintenance status`."
            )
        elif quiet_hours.active() and name in maintenance.MUTED_LEVELS:
            reason = (
                "Quiet hours are on and hold this level; it is posted in the "
                "summary when they end. Critical alerts still come through."
            )
        else:
            reason = (
                "The alert channel was not found. Check `ALERT_CHANNEL_ID` "
                "and that the bot can see and write in that channel."
            )

        await interaction.followup.send(
            f"The {name} test alert was not posted. {reason}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AlertCog(bot))
