"""
/bot restart: restart the bot's own container from Discord.

Only the users in PORTAINER_ALLOWED_USER_IDS, only when Portainer is
set up, and only after pressing Restart. /container restart refuses the
bot's own container; this command is the way to do it on purpose. The
bot is back in about half a minute; buttons on open messages are marked
as expired when it starts again.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from core import activity_log
from modules.containers.portainer_service import PortainerError, PortainerService
from utils import theme
from utils.embeds import create_embed
from utils.logger import logger
from utils.open_messages import ExpiringView


STEP_TIMEOUT = 120

# Background tasks are kept so they are not collected before they run.
_tasks: set[asyncio.Task] = set()


class RestartView(ExpiringView):
    """Restart and Cancel, for the person who ran the command."""

    def __init__(self, user_id: int):
        super().__init__(timeout=STEP_TIMEOUT)
        self.user_id = user_id
        self.interaction: discord.Interaction | None = None

        restart = discord.ui.Button(label="Restart now", style=discord.ButtonStyle.danger)
        restart.callback = self._restart
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(restart)
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.user.id != self.user_id
            or not PortainerService().is_user_allowed(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Only the person who started this can use these controls.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Restart cancelled.", embed=None, view=None
        )

    async def _restart(self, interaction: discord.Interaction) -> None:
        self.stop()
        service = PortainerService()

        try:
            container = await service.own_container()
        except PortainerError as ex:
            await interaction.response.edit_message(
                content=f"Not restarted: {ex}", embed=None, view=None
            )
            return

        activity_log.record("bot_restart", user=interaction.user.id)
        logger.warning(
            "[Bot] Restart requested by %s (%s).",
            interaction.user,
            interaction.user.id,
        )

        await interaction.response.edit_message(
            content=(
                f"Restarting `{container['name']}` now. The bot is back in "
                "about half a minute."
            ),
            embed=None,
            view=None,
        )

        task = asyncio.create_task(self._send_restart(service))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    @staticmethod
    async def _send_restart(service: PortainerService) -> None:
        try:
            await service.restart_own_container()
        except PortainerError as ex:
            logger.error("[Bot] Restart refused: %s", ex)
        except Exception as ex:
            # The connection is cut when Docker stops this process.
            logger.info("[Bot] Restart request ended: %s", ex)


class BotCog(commands.Cog):
    """The /bot command group."""

    control_group = app_commands.Group(
        name="bot",
        description="Control the bot itself.",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @control_group.command(
        name="restart",
        description="Restart the bot's own container.",
    )
    async def restart(self, interaction: discord.Interaction):
        service = PortainerService()

        if not service.is_user_allowed(interaction.user.id):
            logger.warning(
                "[Bot] %s (%s) tried /bot restart.",
                interaction.user,
                interaction.user.id,
            )
            await interaction.response.send_message(
                "You are not allowed to control containers.",
                ephemeral=True,
            )
            return

        if not service.configured:
            await interaction.response.send_message(
                "Portainer is not configured. Set PORTAINER_URL, "
                "PORTAINER_API_KEY and PORTAINER_ENDPOINT_ID in .env.",
                ephemeral=True,
            )
            return

        view = RestartView(interaction.user.id)
        view.interaction = interaction

        await interaction.response.send_message(
            embed=create_embed(
                interaction=interaction,
                title=f"{theme.WARNING_EMOJI} Restart the bot?",
                description=(
                    "The bot's container is restarted through Portainer. "
                    "Monitoring stops for about half a minute, and buttons "
                    "on open messages stop working until you run their "
                    "command again."
                ),
                color=theme.WARNING_COLOR,
            ),
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BotCog(bot))
