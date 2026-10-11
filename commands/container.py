"""
/container commands: manage Docker containers through Portainer.

Only the Discord users listed in PORTAINER_ALLOWED_USER_IDS may use
them, and every reply is private to the person who ran the command.

    /container list [show]
    /container info <name>       details, with Start/Restart/Stop/Logs
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

Layout follows the /torrent commands: create_embed() with the theme
colors, "Emoji Title — Subtitle" titles, em-dash state labels,
Discord timestamps, and confirmation messages that name the target and
the consequence.
"""

import math
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from services.portainer_service import PortainerError, PortainerService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger
from utils.open_messages import ExpiringView


# Every confirmation step expires after this many seconds.
STEP_TIMEOUT = 30

# Lists and the info panel stay usable this long.
PANEL_TIMEOUT = 180

PAGE_SIZE = 15

LOG_LINES_DEFAULT = 50
LOG_LINES_MAX = 200

# An embed description is limited to 4096 characters.
LOG_CHARS = 3800

FILTER_LABELS = {
    "all": "All",
    "running": "Running",
    "stopped": "Not running",
}

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

# Exit codes that mean "stopped on purpose": clean exit, SIGKILL
# (docker kill, or a stop that ran out of grace) and SIGTERM.
CALM_EXIT_CODES = {0, 137, 143}

NOT_ALLOWED = "You are not allowed to use the container commands."


# ----------------------------------------------------------------
# Presentation helpers
# ----------------------------------------------------------------


def _service() -> PortainerService:
    return PortainerService()


def _title(text: str) -> str:
    return f"{theme.CONTAINER_EMOJI} Docker — {text}"


def _name(value: str) -> str:
    return discord.utils.escape_markdown(value)


def _state_style(container: dict) -> tuple[str, str, int]:
    """(dot, label, color) for a container, from its state and health."""
    state = container["state"]
    health = container["health"]
    exit_code = container["exit_code"]

    if state == "running":
        if health == "healthy":
            return "🟢", "Running — Healthy", theme.SUCCESS_COLOR
        if health == "unhealthy":
            return "🔴", "Running — Unhealthy", theme.ERROR_COLOR
        if health == "starting":
            return "🟡", "Running — Starting", theme.WARNING_COLOR
        return "🟢", "Running", theme.SUCCESS_COLOR

    if state == "paused":
        return "🟡", "Paused", theme.WARNING_COLOR

    if state == "restarting":
        return "🟡", "Restarting", theme.WARNING_COLOR

    if state == "exited":
        if exit_code is None:
            return "⚪", "Stopped — Exited", theme.NEUTRAL_COLOR
        label = f"Stopped — Exited ({exit_code})"
        if exit_code in CALM_EXIT_CODES:
            return "⚪", label, theme.NEUTRAL_COLOR
        return "🔴", label, theme.ERROR_COLOR

    if state == "created":
        return "⚪", "Created", theme.NEUTRAL_COLOR

    if state == "dead":
        return "🔴", "Dead", theme.ERROR_COLOR

    return "⚪", (state or "unknown").capitalize(), theme.NEUTRAL_COLOR


def _epoch(value: str) -> int | None:
    """A Docker timestamp as Unix seconds, or None when it never happened."""
    if not value or value.startswith("0001-"):
        return None

    try:
        moment = datetime.fromisoformat(value[:19]).replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None

    return int(moment.timestamp())


def _when(value: str) -> str:
    stamp = _epoch(value)

    if stamp is None:
        return "Never"

    return f"<t:{stamp}:f>\n<t:{stamp}:R>"


def _expires_line() -> str:
    stamp = int(discord.utils.utcnow().timestamp()) + STEP_TIMEOUT
    return f"Expires <t:{stamp}:R>."


def _error_embed(interaction: discord.Interaction, text: str) -> discord.Embed:
    return create_embed(
        interaction=interaction,
        title=f"{theme.ERROR_EMOJI} Docker — Error",
        description=text,
        color=theme.ERROR_COLOR,
    )


def _notice_embed(
    interaction: discord.Interaction,
    title: str,
    text: str,
    color: int = theme.PRIMARY_COLOR,
) -> discord.Embed:
    return create_embed(
        interaction=interaction,
        title=title,
        description=text,
        color=color,
    )


async def _present(
    interaction: discord.Interaction,
    embed: discord.Embed,
    view: "OwnerView | None" = None,
    new: bool = False,
) -> None:
    """
    Show an embed (and view) in the right way for this interaction.

    new=True  : first reply after a deferred slash command.
    otherwise : replace the message the interaction came from, either
                directly or, when already answered, by editing it.
    """
    if new:
        if view is None:
            message = await interaction.followup.send(
                embed=embed,
                ephemeral=True,
                wait=True,
            )
        else:
            message = await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
                wait=True,
            )
    elif interaction.response.is_done():
        message = await interaction.edit_original_response(
            embed=embed,
            view=view,
        )
    else:
        await interaction.response.edit_message(embed=embed, view=view)
        message = interaction.message

    if view is not None:
        view.interaction = interaction
        view.embed = embed

        if message is not None:
            view.message = message


async def _reply_error(interaction: discord.Interaction, text: str) -> None:
    """Send a private error, whether or not the interaction was answered."""
    embed = _error_embed(interaction, text)

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


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


# ----------------------------------------------------------------
# Embeds
# ----------------------------------------------------------------


def _list_embed(
    interaction: discord.Interaction,
    containers: list[dict],
    show: str,
    page: int,
) -> tuple[discord.Embed, int, int]:
    """The list embed for one page. Returns (embed, page, total_pages)."""
    if show == "running":
        shown = [c for c in containers if c["state"] == "running"]
    elif show == "stopped":
        shown = [c for c in containers if c["state"] != "running"]
    else:
        shown = list(containers)

    # Running first, then by name.
    shown.sort(key=lambda c: (c["state"] != "running", c["name"].lower()))

    total_pages = max(1, math.ceil(len(shown) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    chunk = shown[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    running = sum(1 for c in containers if c["state"] == "running")
    header = (
        f"**{running}** running • "
        f"**{len(containers) - running}** not running"
    )

    if chunk:
        lines = []

        for container in chunk:
            dot, label, _ = _state_style(container)
            lines.append(f"{dot} **{_name(container['name'])}** — {label}")

        body = "\n".join(lines)
    else:
        body = "No containers match this filter."

    embed = create_embed(
        interaction=interaction,
        title=_title("Containers"),
        description=f"{header}\n\n{body}",
    )
    embed.set_footer(
        text=(
            f"{FILTER_LABELS.get(show, 'All')} • "
            f"{len(shown)} "
            f"{'container' if len(shown) == 1 else 'containers'} • "
            f"Page {page + 1}/{total_pages}"
        )
    )

    return embed, page, total_pages


def _info_embed(
    interaction: discord.Interaction,
    container: dict,
    details: dict,
    notice: str | None = None,
) -> discord.Embed:
    """The details panel for one container."""
    dot, label, color = _state_style(container)

    fields = [
        EmbedField("Status", f"{dot} {label}", True),
        EmbedField("Image", f"`{details['image'] or 'unknown'}`", True),
        EmbedField("Restarts", str(details["restart_count"]), True),
    ]

    if details["running"]:
        fields.append(
            EmbedField("Started", _when(details["started_at"]), True)
        )
    else:
        fields.append(
            EmbedField("Stopped", _when(details["finished_at"]), True)
        )
        fields.append(
            EmbedField("Exit Code", str(details["exit_code"]), True)
        )

    if _service().protection_reason(container):
        fields.append(
            EmbedField(
                "Protected",
                "Cannot be stopped, restarted, killed or removed "
                "from Discord.",
                False,
            )
        )

    return create_embed(
        interaction=interaction,
        title=_title(_name(container["name"])),
        description=notice or "Live details from Portainer.",
        color=color,
        fields=fields,
    )


async def _logs_embed(
    interaction: discord.Interaction,
    container: dict,
    lines: int,
) -> discord.Embed:
    """The log embed for one container. Raises PortainerError."""
    text = (await _service().logs(container["id"], lines)).rstrip()
    title = _title(f"{_name(container['name'])} Logs")

    if not text:
        return create_embed(
            interaction=interaction,
            title=title,
            description="This container has no log output.",
            color=theme.NEUTRAL_COLOR,
        )

    cut = False

    if len(text) > LOG_CHARS:
        text = text[-LOG_CHARS:].split("\n", 1)[-1]
        cut = True

    text = text.replace("```", "'''")

    embed = create_embed(
        interaction=interaction,
        title=title,
        description=f"```\n{text}\n```",
    )
    embed.set_footer(
        text=(
            f"Last {lines} {'line' if lines == 1 else 'lines'}"
            f"{' • Cut to fit Discord' if cut else ''}"
        ),
    )

    return embed


# ----------------------------------------------------------------
# Running actions
# ----------------------------------------------------------------


async def _run(
    action: str,
    name: str,
    expected_id: str | None = None,
) -> tuple[bool, str]:
    """
    Resolve the container again and run the action on it.

    Looking it up again just before acting means a container that was
    recreated since it was picked is not touched by mistake.
    Returns (succeeded, message).
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
            return True, f"`{name}` removed. Its volumes were not removed."

        outcome = await service.perform(action, container)

        if outcome == "unchanged":
            return True, f"`{name}` {UNCHANGED.get(action, 'was not changed')}."

        return True, f"`{name}` {DONE[action]}."

    except PortainerError as ex:
        return False, str(ex)

    except Exception:
        logger.exception("[Container] %s of %s failed", action, name)
        return False, "Something went wrong. Check the bot log."


async def _execute(
    interaction: discord.Interaction,
    action: str,
    container: dict,
) -> None:
    """
    Run a confirmed action and show the outcome in the same message.

    After anything but remove the message becomes the container's
    details panel with the outcome on top.
    """
    name = container["name"]

    await _present(
        interaction,
        _notice_embed(
            interaction,
            _title(_name(name)),
            f"{PROGRESS[action]} `{name}`...",
            theme.INFO_COLOR,
        ),
    )

    ok, text = await _run(action, name, container["id"])

    logger.info(
        "[Container] %s asked to %s %s: %s",
        interaction.user,
        action,
        name,
        text,
    )

    prefix = theme.SUCCESS_EMOJI if ok else theme.ERROR_EMOJI

    try:
        if action == "remove" and ok:
            await _present(
                interaction,
                _notice_embed(
                    interaction,
                    f"{theme.SUCCESS_EMOJI} Docker — Container Removed",
                    text,
                    theme.SUCCESS_COLOR,
                ),
            )
        else:
            await _show_panel(interaction, name, notice=f"{prefix} {text}")
    except discord.HTTPException as ex:
        logger.warning("[Container] Could not show the result: %s", ex)


async def _show_panel(
    interaction: discord.Interaction,
    name: str,
    notice: str | None = None,
    new: bool = False,
) -> None:
    """Fetch a container and show its details panel."""
    service = _service()

    try:
        container = await service.find_container(name)
        details = await service.inspect(container["id"])
    except PortainerError as ex:
        await _present(interaction, _error_embed(interaction, str(ex)), new=new)
        return
    except Exception:
        logger.exception("[Container] Could not load %s", name)
        await _present(
            interaction,
            _error_embed(interaction, "Something went wrong. Check the bot log."),
            new=new,
        )
        return

    await _present(
        interaction,
        _info_embed(interaction, container, details, notice),
        InfoView(interaction.user.id, container),
        new=new,
    )


async def _vet(
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
        await _reply_error(interaction, str(ex))
        return None

    reason = service.protection_reason(container)

    if reason:
        await _reply_error(
            interaction,
            f"{reason} {action.capitalize()} is not allowed from "
            "Discord for it.",
        )
        return None

    if action == "remove" and container["state"] == "running":
        await _reply_error(
            interaction,
            f"`{container['name']}` is running. Stop it first.",
        )
        return None

    return container


def _confirm_embed(
    interaction: discord.Interaction,
    container: dict,
    question: str,
    consequence: str,
    color: int = theme.WARNING_COLOR,
    emoji: str = theme.WARNING_EMOJI,
    step: str | None = None,
) -> discord.Embed:
    """A confirmation: bold question, the name, then what will happen."""
    dot, label, _ = _state_style(container)

    parts = [f"**{question}**", f"`{container['name']}`", consequence]

    if step:
        parts.append(step)

    parts.append(_expires_line())

    return create_embed(
        interaction=interaction,
        title=f"{emoji} Docker — Confirm",
        description="\n\n".join(parts),
        color=color,
        fields=[
            EmbedField("Status", f"{dot} {label}", True),
            EmbedField("Image", f"`{container['image']}`", True),
        ],
    )


# ----------------------------------------------------------------
# Views
# ----------------------------------------------------------------


class OwnerView(ExpiringView):
    """A view that only the person who started it can use."""

    def __init__(self, user_id: int, timeout: float = STEP_TIMEOUT):
        super().__init__(timeout=timeout)
        self.user_id = user_id

        # The interaction whose message shows this view, so that the
        # buttons can be disabled when it times out.
        self.interaction: discord.Interaction | None = None

        # The embed currently shown with this view.
        self.embed: discord.Embed | None = None

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

    def _disable_all_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    def _expired_embed(self) -> discord.Embed | None:
        """The embed to show after timing out, or None to keep it."""
        return None

    async def on_timeout(self) -> None:
        self._disable_all_buttons()

        if self.interaction is None:
            return

        embed = self._expired_embed()
        kwargs = {"view": self}

        if embed is not None:
            kwargs["embed"] = embed

        try:
            await self.interaction.edit_original_response(**kwargs)
        except discord.HTTPException:
            pass


class ListView(OwnerView):
    """Previous / page / next / refresh for /container list."""

    def __init__(
        self,
        user_id: int,
        containers: list[dict],
        show: str,
    ):
        super().__init__(user_id, timeout=PANEL_TIMEOUT)
        self.containers = containers
        self.show = show
        self.page = 0
        self.total_pages = 1

        self.previous_button = discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.secondary,
        )
        self.page_button = discord.ui.Button(
            label="1 / 1",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.next_button = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
        )
        self.refresh_button = discord.ui.Button(
            label="Refresh",
            style=discord.ButtonStyle.primary,
        )

        self.previous_button.callback = self._previous
        self.next_button.callback = self._next
        self.refresh_button.callback = self._refresh

        self.add_item(self.previous_button)
        self.add_item(self.page_button)
        self.add_item(self.next_button)
        self.add_item(self.refresh_button)

    def render(self, interaction: discord.Interaction) -> discord.Embed:
        embed, self.page, self.total_pages = _list_embed(
            interaction,
            self.containers,
            self.show,
            self.page,
        )

        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1
        self.page_button.label = f"{self.page + 1} / {self.total_pages}"

        return embed

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page -= 1
        await interaction.response.edit_message(
            embed=self.render(interaction),
            view=self,
        )

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await interaction.response.edit_message(
            embed=self.render(interaction),
            view=self,
        )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            self.containers = await _service().list_containers(fresh=True)
        except PortainerError as ex:
            await interaction.followup.send(
                embed=_error_embed(interaction, str(ex)),
                ephemeral=True,
            )
            return

        await interaction.edit_original_response(
            embed=self.render(interaction),
            view=self,
        )


class InfoView(OwnerView):
    """Start / Restart / Stop / Logs / Refresh for one container."""

    def __init__(self, user_id: int, container: dict):
        super().__init__(user_id, timeout=PANEL_TIMEOUT)
        self.container = container
        self.name = container["name"]

        running = container["state"] == "running"
        protected = bool(_service().protection_reason(container))

        self.start_button = discord.ui.Button(
            label="Start",
            style=discord.ButtonStyle.success,
            disabled=running,
            row=0,
        )
        self.restart_button = discord.ui.Button(
            label="Restart",
            style=discord.ButtonStyle.primary,
            disabled=protected,
            row=0,
        )
        self.stop_button = discord.ui.Button(
            label="Stop",
            style=discord.ButtonStyle.danger,
            disabled=protected or not running,
            row=0,
        )
        self.logs_button = discord.ui.Button(
            label="Logs",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.refresh_button = discord.ui.Button(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

        self.start_button.callback = self._start
        self.restart_button.callback = self._restart
        self.stop_button.callback = self._stop
        self.logs_button.callback = self._logs
        self.refresh_button.callback = self._refresh

        for button in (
            self.start_button,
            self.restart_button,
            self.stop_button,
            self.logs_button,
            self.refresh_button,
        ):
            self.add_item(button)

    async def _start(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        await _execute(interaction, "start", self.container)

    async def _restart(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        await _confirm_simple(interaction, "restart", self.name)

    async def _stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        await _confirm_simple(interaction, "stop", self.name)

    async def _logs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            embed = await _logs_embed(
                interaction,
                self.container,
                LOG_LINES_DEFAULT,
            )
        except PortainerError as ex:
            embed = _error_embed(interaction, str(ex))

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        await _show_panel(interaction, self.name)


class ConfirmView(OwnerView):
    """
    A confirm and a cancel button.

    `on_confirm(interaction, view)` receives the button press and must
    answer it. It stops the view itself once the flow moves on, so the
    expiry note cannot overwrite what comes next. Cancelling returns to
    the container's details panel.
    """

    def __init__(
        self,
        user_id: int,
        container_name: str,
        confirm_label: str,
        cancel_notice: str,
        on_confirm,
        danger: bool = False,
    ):
        super().__init__(user_id)
        self.container_name = container_name
        self.cancel_notice = cancel_notice
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

    def _expired_embed(self) -> discord.Embed | None:
        if self.embed is None:
            return None

        embed = self.embed.copy()
        base = (embed.description or "").rsplit("\n\n", 1)[0]
        embed.description = f"{base}\n\nExpired — run the command again."
        embed.colour = discord.Color(theme.NEUTRAL_COLOR)

        return embed

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await self.on_confirm(interaction, self)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.defer()
        await _show_panel(
            interaction,
            self.container_name,
            notice=f"{theme.INFO_EMOJI} {self.cancel_notice}",
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
            await interaction.response.defer()
            await _show_panel(
                interaction,
                self.container_name,
                notice=(
                    f"{theme.ERROR_EMOJI} The name did not match. "
                    "Nothing was changed."
                ),
            )
            return

        await self.on_match(interaction)


# ----------------------------------------------------------------
# Confirmation flows
# ----------------------------------------------------------------


async def _confirm_simple(
    interaction: discord.Interaction,
    action: str,
    name: str,
    new: bool = False,
) -> None:
    """Stop and restart: one confirmation."""
    container = await _vet(interaction, action, name)

    if container is None:
        return

    async def go(button_interaction, view):
        view.stop()
        await button_interaction.response.defer()
        await _execute(button_interaction, action, container)

    consequence = {
        "stop": (
            "This sends a stop signal and waits up to "
            "10 seconds before forcing it."
        ),
        "restart": (
            "This stops the container, waits up to 10 seconds for it "
            "to exit, and starts it again."
        ),
    }[action]

    await _present(
        interaction,
        _confirm_embed(
            interaction,
            container,
            f"{action.capitalize()} container?",
            consequence,
        ),
        ConfirmView(
            interaction.user.id,
            container["name"],
            action.capitalize(),
            f"{action.capitalize()} cancelled.",
            go,
        ),
        new=new,
    )


async def _confirm_kill(
    interaction: discord.Interaction,
    name: str,
    new: bool = False,
) -> None:
    """Kill: a confirmation, then typing the name."""
    container = await _vet(interaction, "kill", name)

    if container is None:
        return

    async def execute(modal_interaction):
        await modal_interaction.response.defer()
        await _execute(modal_interaction, "kill", container)

    async def ask(button_interaction, view):
        await button_interaction.response.send_modal(
            NameModal(
                f"Kill {container['name']}",
                container["name"],
                view,
                execute,
            )
        )

    await _present(
        interaction,
        _confirm_embed(
            interaction,
            container,
            "Kill container?",
            "This sends SIGKILL at once, without a clean shutdown. "
            "Data being written can be lost.",
            color=theme.ERROR_COLOR,
            emoji=theme.ERROR_EMOJI,
        ),
        ConfirmView(
            interaction.user.id,
            container["name"],
            "Kill",
            "Kill cancelled.",
            ask,
            danger=True,
        ),
        new=new,
    )


async def _confirm_remove(
    interaction: discord.Interaction,
    name: str,
    new: bool = False,
) -> None:
    """Remove: a confirmation, typing the name, a final confirmation."""
    container = await _vet(interaction, "remove", name)

    if container is None:
        return

    async def execute(button_interaction, view):
        view.stop()
        await button_interaction.response.defer()
        await _execute(button_interaction, "remove", container)

    async def final(modal_interaction):
        # The modal answer is the interaction that shows step 3.
        await modal_interaction.response.defer()

        await _present(
            modal_interaction,
            _confirm_embed(
                modal_interaction,
                container,
                "Permanently remove container?",
                "This deletes the container. Its volumes are kept. "
                "This cannot be undone.",
                color=theme.ERROR_COLOR,
                emoji=theme.ERROR_EMOJI,
                step="Step 3 of 3.",
            ),
            ConfirmView(
                modal_interaction.user.id,
                container["name"],
                "Remove permanently",
                "Removal cancelled.",
                execute,
                danger=True,
            ),
        )

    async def ask(button_interaction, view):
        await button_interaction.response.send_modal(
            NameModal(
                f"Remove {container['name']}",
                container["name"],
                view,
                final,
            )
        )

    await _present(
        interaction,
        _confirm_embed(
            interaction,
            container,
            "Remove container?",
            "The container will be deleted. Its volumes are kept.",
            color=theme.ERROR_COLOR,
            emoji=theme.ERROR_EMOJI,
            step="Step 1 of 3.",
        ),
        ConfirmView(
            interaction.user.id,
            container["name"],
            "Continue",
            "Removal cancelled.",
            ask,
            danger=True,
        ),
        new=new,
    )


# ----------------------------------------------------------------
# The command group
# ----------------------------------------------------------------


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

        try:
            containers = await _service().list_containers(fresh=True)
        except PortainerError as ex:
            await interaction.followup.send(
                embed=_error_embed(interaction, str(ex)),
                ephemeral=True,
            )
            return

        view = ListView(interaction.user.id, containers, show)
        await _present(interaction, view.render(interaction), view, new=True)

    @container.command(
        name="info",
        description="Show the details of one container.",
    )
    @app_commands.describe(name="Container name.")
    async def info(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        await _show_panel(interaction, name, new=True)

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

        try:
            container = await _service().find_container(name)
            embed = await _logs_embed(interaction, container, lines)
        except PortainerError as ex:
            embed = _error_embed(interaction, str(ex))

        await interaction.followup.send(embed=embed, ephemeral=True)

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

        ok, text = await _run("start", name)

        logger.info(
            "[Container] %s asked to start %s: %s",
            interaction.user,
            name,
            text,
        )

        prefix = theme.SUCCESS_EMOJI if ok else theme.ERROR_EMOJI
        await _show_panel(interaction, name, notice=f"{prefix} {text}", new=True)

    @container.command(
        name="stop",
        description="Stop a container.",
    )
    @app_commands.describe(name="Container name.")
    async def stop(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        await _confirm_simple(interaction, "stop", name, new=True)

    @container.command(
        name="restart",
        description="Restart a container.",
    )
    @app_commands.describe(name="Container name.")
    async def restart(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        await _confirm_simple(interaction, "restart", name, new=True)

    @container.command(
        name="kill",
        description="Kill a container immediately (SIGKILL).",
    )
    @app_commands.describe(name="Container name.")
    async def kill(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        await _confirm_kill(interaction, name, new=True)

    @container.command(
        name="remove",
        description="Remove a stopped container.",
    )
    @app_commands.describe(name="Container name.")
    async def remove(self, interaction: discord.Interaction, name: str):
        if not await _authorize(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        await _confirm_remove(interaction, name, new=True)

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
