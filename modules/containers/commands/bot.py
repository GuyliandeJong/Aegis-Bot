"""
/bot restart and /bot update: control the bot itself from Discord.

/bot restart restarts the bot's own container from Discord.

Only the users in PORTAINER_ALLOWED_USER_IDS, only when Portainer is
set up, and only after pressing Restart. /container restart refuses the
bot's own container; this command is the way to do it on purpose. The
bot is back in about half a minute; buttons on open messages are marked
as expired when it starts again.

/bot update [branch] asks the server to fetch a branch (main by default),
fast-forward to it and restart the bot, like `git try` and `git back`
do by hand. The bot cannot run commands on the server, so it leaves a
request file that a systemd path unit on the host picks up at once (see
modules/containers/update_service.py and scripts/update_host.sh); the
host script reports the result through the webhook of the watchdog. It
needs REMOTE_UPDATE=true, so it stays off until that host service is
installed.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from core import activity_log
from modules.containers import update_service
from modules.containers.portainer_service import PortainerError, PortainerService
from utils import theme
from utils.embeds import create_embed
from utils.logger import logger
from utils.open_messages import ExpiringView


STEP_TIMEOUT = 120

# Background tasks are kept so they are not collected before they run.
_tasks: set[asyncio.Task] = set()


class OwnerView(ExpiringView):
    """A confirmation only the person who ran the command can answer."""

    def __init__(self, user_id: int):
        super().__init__(timeout=STEP_TIMEOUT)
        self.user_id = user_id
        self.interaction: discord.Interaction | None = None

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



class RestartView(OwnerView):
    """Restart and Cancel, for the person who ran the command."""

    def __init__(self, user_id: int):
        super().__init__(user_id)
        restart = discord.ui.Button(label="Restart now", style=discord.ButtonStyle.danger)
        restart.callback = self._restart
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(restart)
        self.add_item(cancel)

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


class UpdateView(OwnerView):
    """Update now and Cancel, for the person who ran the command."""

    def __init__(self, user_id: int, branch: str):
        super().__init__(user_id)
        self.branch = branch
        update = discord.ui.Button(label="Update now", style=discord.ButtonStyle.danger)
        update.callback = self._update
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(update)
        self.add_item(cancel)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Update cancelled.", embed=None, view=None
        )

    async def _update(self, interaction: discord.Interaction) -> None:
        self.stop()

        try:
            request_id = update_service.write_request(self.branch, interaction.user.id)
        except (OSError, ValueError) as ex:
            logger.error("[Bot] Could not leave the update request: %s", ex)
            await interaction.response.edit_message(
                content=f"The update request could not be written: {ex}",
                embed=None,
                view=None,
            )
            return

        activity_log.record("bot_update", user=interaction.user.id, branch=self.branch)
        logger.warning(
            "[Bot] Update to %s requested by %s (%s) as %s.",
            self.branch,
            interaction.user,
            interaction.user.id,
            request_id,
        )

        await interaction.response.edit_message(
            content=(
                f"Update to `{self.branch}` requested (`{request_id}`). The "
                "server starts it within a moment; the result is posted by "
                "the webhook once the bot is back."
            ),
            embed=None,
            view=None,
        )


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

    @control_group.command(
        name="update",
        description="Fetch a branch on the server and restart the bot with it.",
    )
    @app_commands.describe(branch="Branch to update to (default: main).")
    async def update(
        self,
        interaction: discord.Interaction,
        branch: str = "main",
    ):
        service = PortainerService()
        branch = branch.strip()

        if not service.is_user_allowed(interaction.user.id):
            logger.warning(
                "[Bot] %s (%s) tried /bot update.",
                interaction.user,
                interaction.user.id,
            )
            await interaction.response.send_message(
                "You are not allowed to control containers.",
                ephemeral=True,
            )
            return

        if not update_service.enabled():
            await interaction.response.send_message(
                "Remote update is off. Install the host service "
                "(`scripts/install_update_service.sh`, see the docs) and "
                "set `REMOTE_UPDATE=true` in .env.",
                ephemeral=True,
            )
            return

        if not update_service.valid_branch(branch):
            await interaction.response.send_message(
                f"`{branch[:60]}` is not a valid branch name.",
                ephemeral=True,
            )
            return

        view = UpdateView(interaction.user.id, branch)
        view.interaction = interaction

        await interaction.response.send_message(
            embed=create_embed(
                interaction=interaction,
                title=f"{theme.WARNING_EMOJI} Update the bot to `{branch}`?",
                description=(
                    "The server fetches the branch, fast-forwards to it and "
                    "restarts the bot, about a minute in total. A branch "
                    "that cannot be fast-forwarded, or a server with "
                    "uncommitted changes, is reported and left alone."
                ),
                color=theme.WARNING_COLOR,
            ),
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BotCog(bot))
