"""
/container commands: manage Docker containers through Portainer.

Only the Discord users listed in PORTAINER_ALLOWED_USER_IDS may use
them, and every reply is private to the person who ran the command.

    /container list [show]
    /container info <name>
    /container logs <name> [lines]
    /container start <name>
    /container stop <name>       one confirmation
    /container restart <name>    one confirmation
    /container kill <name>       confirmation, then typing the name
    /container remove <name>     confirmation, typing the name, and a
                                 final confirmation

The Portainer calls are in services/portainer_service.py. Protected
containers cannot be stopped, restarted, killed or removed; that is
enforced there, not here.
"""

import discord
from discord import app_commands
from discord.ext import commands

from services.portainer_service import PortainerError, PortainerService
from utils.logger import logger
from utils.open_messages import ExpiringView


# Every confirmation step expires after this many seconds.
STEP_TIMEOUT = 30

LOG_LINES_DEFAULT = 50
LOG_LINES_MAX = 200

# Discord messages are limited to 2000 characters.
LOG_CHARS = 1800
LIST_CHARS = 3800

PROGRESS = {
    "start": "Starting",
    "stop": "Stopping",
    "restart": "Restarting",
    "kill": "Killing",
    "remove": "Removing",
}

DONE = {
    "start": "started",
    "stop": "stopped",
    "restart": "restarted",
    "kill": "killed",
}

UNCHANGED = {
    "start": "was already running",
    "stop": "was already stopped",
}

NOT_ALLOWED = "You are not allowed to use the container commands."


def _service() -> PortainerService:
    return PortainerService()


async def _authorize(interaction: discord.Interaction) -> bool:
    """Allow the configured users only, and only when Portainer is set up."""
    service = _service()

    if not service.is_user_allowed(interaction.user.id):
        logger.warning(
            "[Container] %s (%s) tried a container command.",
            interaction.user,
            interaction.user.id,
        )
        await interaction.response.send_message(
            NOT_ALLOWED,
            ephemeral=True,
        )
        return False

    if not service.configured:
        await interaction.response.send_message(
            "Portainer is not configured. Set PORTAINER_URL, "
            "PORTAINER_API_KEY and PORTAINER_ENDPOINT_ID in .env.",
            ephemeral=True,
        )
        return False

    return True


def _timestamp(value: str) -> str:
    """A Docker timestamp as 'YYYY-MM-DD HH:MM:SS UTC'."""
    if not value or value.startswith("0001-"):
        return "never"

    return value[:19].replace("T", " ") + " UTC"


async def _run(
    action: str,
    name: str,
    expected_id: str | None = None,
) -> str:
    """
    Resolve the container again and run the action on it.

    Looking it up again just before acting means a container that was
    recreated since it was picked is not touched by mistake.
    """
    service = _service()

    try:
        container = await service.find_container(name)

        if expected_id is not None and container["id"] != expected_id:
            raise PortainerError(
                f"`{name}` was recreated since you picked it. "
                "Run the command again."
            )

        if action == "remove":
            await service.remove(container)
            return f"`{name}` removed. Its volumes were not removed."

        outcome = await service.perform(action, container)

        if outcome == "unchanged":
            return f"`{name}` {UNCHANGED.get(action, 'was not changed')}."

        return f"`{name}` {DONE[action]}."

    except PortainerError as ex:
        return str(ex)

    except Exception:
        logger.exception("[Container] %s of %s failed", action, name)
        return "Something went wrong. Check the bot log."


async def _execute(
    interaction: discord.Interaction,
    action: str,
    name: str,
    expected_id: str,
) -> None:
    """Run a confirmed action and show the result in the same message."""
    await interaction.response.edit_message(
        content=f"{PROGRESS[action]} `{name}`...",
        view=None,
    )

    result = await _run(action, name, expected_id)

    logger.info(
        "[Container] %s asked to %s %s: %s",
        interaction.user,
        action,
        name,
        result,
    )

    try:
        await interaction.edit_original_response(content=result)
    except discord.HTTPException as ex:
        logger.warning("[Container] Could not show the result: %s", ex)


class OwnerView(ExpiringView):
    """A view that only the person who started it can use."""

    def __init__(self, user_id: int):
        super().__init__(timeout=STEP_TIMEOUT)
        self.user_id = user_id

        # The interaction whose message shows this view, so that it
        # can say it has expired.
        self.interaction: discord.Interaction | None = None

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if (
            interaction.user.id != self.user_id
            or not _service().is_user_allowed(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Only the person who started this can use these controls.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self) -> None:
        if self.interaction is None:
            return

        try:
            await self.interaction.edit_original_response(
                content="Expired. Run the command again.",
                view=None,
            )
        except discord.HTTPException:
            pass


class ConfirmView(OwnerView):
    """
    A confirm and a cancel button.

    `on_confirm(interaction, view)` receives the button press and must
    answer it. It stops the view itself once the flow moves on, so the
    expiry note cannot overwrite what comes next.
    """

    def __init__(
        self,
        user_id: int,
        confirm_label: str,
        on_confirm,
        danger: bool = False,
    ):
        super().__init__(user_id)
        self.on_confirm = on_confirm

        confirm = discord.ui.Button(
            label=confirm_label,
            style=(
                discord.ButtonStyle.danger
                if danger
                else discord.ButtonStyle.primary
            ),
        )
        confirm.callback = self._confirm

        cancel = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )
        cancel.callback = self._cancel

        self.add_item(confirm)
        self.add_item(cancel)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await self.on_confirm(interaction, self)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Cancelled.",
            view=None,
        )


class NameModal(discord.ui.Modal):
    """Asks for the container name to be typed before going on."""

    def __init__(
        self,
        title: str,
        container_name: str,
        parent: OwnerView,
        on_match,
    ):
        super().__init__(title=title[:45], timeout=STEP_TIMEOUT)
        self.container_name = container_name
        self.parent = parent
        self.on_match = on_match

        self.answer = discord.ui.TextInput(
            label="Type the container name to continue",
            max_length=100,
            required=True,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent.stop()

        if self.answer.value.strip().lower() != self.container_name.lower():
            await interaction.response.edit_message(
                content="The name did not match. Cancelled.",
                view=None,
            )
            return

        await self.on_match(interaction)


class ContainerCog(commands.Cog):
    """The /container command group."""

    container = app_commands.Group(
        name="container",
        description="Manage Docker containers through Portainer.",
    )

    @staticmethod
    async def _names(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Container names for autocomplete. Nothing for other users."""
        service = _service()

        if not service.configured or not service.is_user_allowed(
            interaction.user.id
        ):
            return []

        try:
            containers = await service.list_containers()
        except PortainerError:
            return []

        wanted = current.lower()

        return [
            app_commands.Choice(name=c["name"], value=c["name"])
            for c in containers
            if wanted in c["name"].lower()
        ][:25]

    async def _pick(
        self,
        interaction: discord.Interaction,
        action: str,
        name: str,
    ) -> dict | None:
        """
        Resolve and vet a container for a confirmed action. Sends the
        reason and returns None when the action must not go ahead.
        """
        service = _service()

        try:
            container = await service.find_container(name)
        except PortainerError as ex:
            await interaction.followup.send(str(ex), ephemeral=True)
            return None

        reason = service.protection_reason(container)

        if reason:
            await interaction.followup.send(
                f"{reason} {action.capitalize()} is not allowed "
                "from Discord for it.",
                ephemeral=True,
            )
            return None

        if action == "remove" and container["state"] == "running":
            await interaction.followup.send(
                f"`{container['name']}` is running. Stop it first.",
                ephemeral=True,
            )
            return None

        return container

    @staticmethod
    async def _send_view(
        interaction: discord.Interaction,
        content: str,
        view: OwnerView,
    ) -> None:
        view.interaction = interaction
        view.message = await interaction.followup.send(
            content,
            view=view,
            ephemeral=True,
            wait=True,
        )

    # ------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------

    @container.command(
        name="list",
        description="List containers and their state.",
    )
    @app_commands.describe(show="Which containers to show.")
    @app_commands.choices(
        show=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Running", value="running"),
            app_commands.Choice(name="Not running", value="stopped"),
        ]
    )
    async def list_containers(
        self,
        interaction: discord.Interaction,
        show: str = "all",
    ):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        service = _service()

        try:
            containers = await service.list_containers(fresh=True)
        except PortainerError as ex:
            await interaction.followup.send(str(ex), ephemeral=True)
            return

        running = sum(1 for c in containers if c["state"] == "running")

        if show == "running":
            shown = [c for c in containers if c["state"] == "running"]
        elif show == "stopped":
            shown = [c for c in containers if c["state"] != "running"]
        else:
            shown = containers

        if not shown:
            await interaction.followup.send(
                "No containers to show.",
                ephemeral=True,
            )
            return

        width = min(max(len(c["name"]) for c in shown), 32)
        lines = []
        used = 0

        for index, container in enumerate(shown):
            line = (
                f"{container['name'][:width]:<{width}}  "
                f"{service.display_state(container)}"
            )

            if used + len(line) + 1 > LIST_CHARS:
                lines.append(f"... and {len(shown) - index} more")
                break

            lines.append(line)
            used += len(line) + 1

        embed = discord.Embed(
            title="Containers",
            description="```\n" + "\n".join(lines) + "\n```",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{running} running of {len(containers)} in total"
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @container.command(
        name="info",
        description="Show the details of one container.",
    )
    @app_commands.describe(name="Container name.")
    async def info(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        service = _service()

        try:
            container = await service.find_container(name)
            details = await service.inspect(container["id"])
        except PortainerError as ex:
            await interaction.followup.send(str(ex), ephemeral=True)
            return

        embed = discord.Embed(
            title=container["name"],
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="State",
            value=service.display_state(container),
        )
        embed.add_field(name="Image", value=details["image"] or "unknown")
        embed.add_field(name="Restarts", value=str(details["restart_count"]))

        if details["running"]:
            embed.add_field(
                name="Started",
                value=_timestamp(details["started_at"]),
            )
        else:
            embed.add_field(
                name="Stopped",
                value=_timestamp(details["finished_at"]),
            )
            embed.add_field(
                name="Exit code",
                value=str(details["exit_code"]),
            )

        if service.protection_reason(container):
            embed.add_field(
                name="Protected",
                value="Cannot be stopped, restarted, killed or removed here.",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @container.command(
        name="logs",
        description="Show the last lines of a container's output.",
    )
    @app_commands.describe(
        name="Container name.",
        lines=f"Number of lines, up to {LOG_LINES_MAX}.",
    )
    async def logs(
        self,
        interaction: discord.Interaction,
        name: str,
        lines: app_commands.Range[int, 1, LOG_LINES_MAX] = LOG_LINES_DEFAULT,
    ):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        service = _service()

        try:
            container = await service.find_container(name)
            text = (await service.logs(container["id"], lines)).rstrip()
        except PortainerError as ex:
            await interaction.followup.send(str(ex), ephemeral=True)
            return

        if not text:
            await interaction.followup.send(
                f"`{container['name']}` has no log output.",
                ephemeral=True,
            )
            return

        note = ""

        if len(text) > LOG_CHARS:
            text = text[-LOG_CHARS:]
            text = text.split("\n", 1)[-1]
            note = " (cut to fit Discord)"

        text = text.replace("```", "'''")

        await interaction.followup.send(
            f"Last {lines} line(s) of `{container['name']}`{note}:\n"
            f"```\n{text}\n```",
            ephemeral=True,
        )

    # ------------------------------------------------------------
    # Changing
    # ------------------------------------------------------------

    @container.command(
        name="start",
        description="Start a container.",
    )
    @app_commands.describe(name="Container name.")
    async def start(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        result = await _run("start", name)

        logger.info(
            "[Container] %s asked to start %s: %s",
            interaction.user,
            name,
            result,
        )

        await interaction.followup.send(result, ephemeral=True)

    @container.command(
        name="stop",
        description="Stop a container.",
    )
    @app_commands.describe(name="Container name.")
    async def stop(self, interaction: discord.Interaction, name: str):
        await self._simple_flow(interaction, "stop", name)

    @container.command(
        name="restart",
        description="Restart a container.",
    )
    @app_commands.describe(name="Container name.")
    async def restart(self, interaction: discord.Interaction, name: str):
        await self._simple_flow(interaction, "restart", name)

    async def _simple_flow(
        self,
        interaction: discord.Interaction,
        action: str,
        name: str,
    ) -> None:
        """Stop and restart: one confirmation."""
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        container = await self._pick(interaction, action, name)

        if container is None:
            return

        async def go(button_interaction, view):
            view.stop()
            await _execute(
                button_interaction,
                action,
                container["name"],
                container["id"],
            )

        await self._send_view(
            interaction,
            f"{action.capitalize()} `{container['name']}` "
            f"({_service().display_state(container)})? "
            f"This expires in {STEP_TIMEOUT} seconds.",
            ConfirmView(
                interaction.user.id,
                f"{action.capitalize()}",
                go,
            ),
        )

    @container.command(
        name="kill",
        description="Kill a container immediately (SIGKILL).",
    )
    @app_commands.describe(name="Container name.")
    async def kill(self, interaction: discord.Interaction, name: str):
        """Kill: a confirmation, then typing the name."""
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        container = await self._pick(interaction, "kill", name)

        if container is None:
            return

        async def execute(modal_interaction):
            await _execute(
                modal_interaction,
                "kill",
                container["name"],
                container["id"],
            )

        async def ask(button_interaction, view):
            await button_interaction.response.send_modal(
                NameModal(
                    f"Kill {container['name']}",
                    container["name"],
                    view,
                    execute,
                )
            )

        await self._send_view(
            interaction,
            f"Kill `{container['name']}`? This sends SIGKILL at once, "
            "without a clean shutdown. "
            f"This expires in {STEP_TIMEOUT} seconds.",
            ConfirmView(
                interaction.user.id,
                "Kill",
                ask,
                danger=True,
            ),
        )

    @container.command(
        name="remove",
        description="Remove a stopped container.",
    )
    @app_commands.describe(name="Container name.")
    async def remove(self, interaction: discord.Interaction, name: str):
        """Remove: a confirmation, typing the name, a final confirmation."""
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        container = await self._pick(interaction, "remove", name)

        if container is None:
            return

        service = _service()

        async def execute(button_interaction, view):
            view.stop()
            await _execute(
                button_interaction,
                "remove",
                container["name"],
                container["id"],
            )

        async def final(modal_interaction):
            view = ConfirmView(
                interaction.user.id,
                "Remove permanently",
                execute,
                danger=True,
            )
            view.interaction = modal_interaction

            await modal_interaction.response.edit_message(
                content=(
                    f"Final confirmation. Remove `{container['name']}` "
                    f"(image `{container['image']}`, "
                    f"{service.display_state(container)})? Its volumes "
                    "are kept. This cannot be undone. "
                    f"This expires in {STEP_TIMEOUT} seconds."
                ),
                view=view,
            )

            view.message = modal_interaction.message

        async def ask(button_interaction, view):
            await button_interaction.response.send_modal(
                NameModal(
                    f"Remove {container['name']}",
                    container["name"],
                    view,
                    final,
                )
            )

        await self._send_view(
            interaction,
            f"Remove `{container['name']}`? Step 1 of 3. "
            f"This expires in {STEP_TIMEOUT} seconds.",
            ConfirmView(
                interaction.user.id,
                "Continue",
                ask,
                danger=True,
            ),
        )

    # ------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------

    @info.autocomplete("name")
    async def info_names(self, interaction, current: str):
        return await self._names(interaction, current)

    @logs.autocomplete("name")
    async def logs_names(self, interaction, current: str):
        return await self._names(interaction, current)

    @start.autocomplete("name")
    async def start_names(self, interaction, current: str):
        return await self._names(interaction, current)

    @stop.autocomplete("name")
    async def stop_names(self, interaction, current: str):
        return await self._names(interaction, current)

    @restart.autocomplete("name")
    async def restart_names(self, interaction, current: str):
        return await self._names(interaction, current)

    @kill.autocomplete("name")
    async def kill_names(self, interaction, current: str):
        return await self._names(interaction, current)

    @remove.autocomplete("name")
    async def remove_names(self, interaction, current: str):
        return await self._names(interaction, current)


async def setup(bot):
    await bot.add_cog(ContainerCog())
