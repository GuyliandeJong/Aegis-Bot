"""
/maintenance commands: mute alerts while working on the server.

    /maintenance start <duration> [reason]
    /maintenance end
    /maintenance status

While maintenance is on, warnings, recoveries, info and success alerts
are not posted; critical alerts still are. The state and the muting
are in services/maintenance.py and NotificationService, which also
posts the start and end messages in the alert channel. Replies to the
command itself are private.
"""

import discord
from discord import app_commands
from discord.ext import commands

from services import maintenance
from services.notification_service import NotificationService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed


DURATIONS = [
    app_commands.Choice(name="30 minutes", value=30),
    app_commands.Choice(name="1 hour", value=60),
    app_commands.Choice(name="2 hours", value=120),
    app_commands.Choice(name="4 hours", value=240),
    app_commands.Choice(name="8 hours", value=480),
    app_commands.Choice(name="24 hours", value=1440),
]

# Keeps the start message well inside an embed description.
REASON_MAX = 200


class MaintenanceCog(commands.Cog):
    """The /maintenance command group."""

    # Administrators only by default; Discord lets this be changed per
    # user or role under Server Settings > Integrations.
    maintenance_group = app_commands.Group(
        name="maintenance",
        description="Mute alerts while working on the server.",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Holds no state of its own; maintenance.py does.
        self.notifications = NotificationService(bot)

    @maintenance_group.command(
        name="start",
        description="Mute all alerts except critical ones for a while.",
    )
    @app_commands.describe(
        duration="How long to mute alerts.",
        reason="Shown in the alert channel.",
    )
    @app_commands.choices(duration=DURATIONS)
    async def start(
        self,
        interaction: discord.Interaction,
        duration: app_commands.Choice[int],
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        reason = (reason or "").strip()[:REASON_MAX]

        # An expired maintenance is reported first, so its muted
        # alerts are not carried over into the new one.
        await self.notifications.end_maintenance_if_expired()

        state = await self.notifications.start_maintenance(
            duration.value,
            reason,
            interaction.user.id,
        )

        await interaction.followup.send(
            embed=create_embed(
                interaction=interaction,
                title="🛠️ Maintenance — On",
                description=(
                    f"Alerts are muted until <t:{state['until']}:t> "
                    f"(<t:{state['until']}:R>), except critical ones. "
                    "End it early with `/maintenance end`."
                ),
                color=theme.PRIMARY_COLOR,
            ),
            ephemeral=True,
        )

    @maintenance_group.command(
        name="end",
        description="End maintenance now and show what was muted.",
    )
    async def end(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        await self.notifications.end_maintenance_if_expired()

        if not maintenance.active():
            await interaction.followup.send(
                "Maintenance is not on.",
                ephemeral=True,
            )
            return

        state = await self.notifications.end_maintenance(
            interaction.user.id
        )
        total = sum((state or {}).get("counts", {}).values())

        await interaction.followup.send(
            f"Maintenance ended. {total} muted "
            f"alert{'s' if total != 1 else ''} listed in the alert channel.",
            ephemeral=True,
        )

    @maintenance_group.command(
        name="status",
        description="Show whether maintenance is on and what it muted so far.",
    )
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        await self.notifications.end_maintenance_if_expired()

        state = maintenance.current()

        if state is None:
            await interaction.followup.send(
                embed=create_embed(
                    interaction=interaction,
                    title="🛠️ Maintenance — Off",
                    description="All alerts are posted.",
                    color=theme.SUCCESS_COLOR,
                ),
                ephemeral=True,
            )
            return

        total, titles = maintenance.muted_summary(state, limit=1000)

        fields = [
            EmbedField(
                "Started",
                f"<t:{state.get('started', state['until'])}:t> by "
                f"<@{state.get('started_by', '?')}>",
                inline=True,
            ),
            EmbedField(
                "Until",
                f"<t:{state['until']}:t> (<t:{state['until']}:R>)",
                inline=True,
            ),
        ]

        if state.get("reason"):
            fields.append(EmbedField("Reason", state["reason"], inline=False))

        fields.append(
            EmbedField(
                f"Muted so far — {total}",
                titles or "Nothing yet.",
                inline=False,
            )
        )

        await interaction.followup.send(
            embed=create_embed(
                interaction=interaction,
                title="🛠️ Maintenance — On",
                description=(
                    "Warnings, recoveries and info messages are muted; "
                    "critical alerts still come through."
                ),
                color=theme.WARNING_COLOR,
                fields=fields,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MaintenanceCog(bot))
