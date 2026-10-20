"""
/help: list the bot's commands that the person may run.

The list is read from the live command tree, so a new command or
subcommand appears without changing this file. Only the area it is
filed under (AREAS) and the administrator notes for checks made inside
a command (ADMIN_ONLY, ADMIN_NOTES) are written out here.

    /help              every command you may run, grouped by area
    /help <command>    one command with its options

Who may run what:
- A command with default permissions (for example administrator, set
  with @app_commands.default_permissions) is shown only to members who
  have those permissions. Discord lets a server change this per user or
  role under Server Settings > Integrations; /help does not see those
  overrides and goes by the default.
- /container is shown only to the users in PORTAINER_ALLOWED_USER_IDS.
- Subcommands in ADMIN_ONLY are shown to administrators only.

The reply is private to the person who asked.
"""

import discord
from discord import app_commands
from discord.ext import commands

from services.portainer_service import PortainerService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger


# Area each top-level command is filed under. A command missing here
# goes under "Other", so a new command is still listed.
AREAS = {
    "torrent": "Torrents",
    "jellyfin": "Jellyfin",
    "container": "Containers",
    "server": "Server",
    "cpu": "Server",
    "memory": "Server",
    "filesystems": "Server",
    "network": "Server",
    "storage": "Server",
    "temperature": "Server",
    "idrac": "Server",
    "esxi": "Server",
    "wan": "Server",
    "wol": "Server",
}

AREA_ORDER = ["Torrents", "Jellyfin", "Server", "Containers", "Other"]

AREA_EMOJI = {
    "Torrents": "🧲",
    "Jellyfin": "🎬",
    "Server": theme.SERVER_EMOJI,
    "Containers": theme.CONTAINER_EMOJI,
    "Other": theme.INFO_EMOJI,
}

# Subcommands share their group's default permissions, so Discord shows
# them to everyone. These check for administrator inside the command
# itself (commands/torrent/cleanup.py) and are hidden from other members.
# Keep this in step with the code when such a check is added or removed.
ADMIN_ONLY = {
    "torrent cleanup",
}

# Subcommands everyone may run, where one part is limited to
# administrators in the code (commands/torrent/speed.py: choosing a mode).
ADMIN_NOTES = {
    "torrent speed": "changing it: administrators",
}

# Discord's embed limits.
FIELD_VALUE_LIMIT = 1024
EMBED_TOTAL_LIMIT = 6000
FIELD_COUNT_LIMIT = 25

DETAIL_DESCRIPTION_LIMIT = 4096


# ----------------------------------------------------------------
# Reading the command tree
# ----------------------------------------------------------------


def is_administrator(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions is not None and permissions.administrator)


def _has_default_permissions(user, command) -> bool:
    """True when the user has the command's default permissions."""
    required = getattr(command, "default_permissions", None)

    if required is None or required.value == 0:
        return True

    permissions = getattr(user, "guild_permissions", None)

    if permissions is None:
        return False

    return permissions.administrator or permissions >= required


def _container_allowed(user) -> bool:
    try:
        return PortainerService().is_user_allowed(user.id)
    except Exception:
        logger.exception("[Help] Reading PORTAINER_ALLOWED_USER_IDS failed.")
        return False


def top_level_commands(tree: app_commands.CommandTree, guild=None) -> list:
    """Slash commands in the tree, global and guild, once each by name."""
    found = {}

    # bot.py copies the global commands to the guild, so both lists
    # usually hold the same commands.
    scopes = [None] if guild is None else [None, guild]

    for scope in scopes:
        for command in tree.get_commands(
            guild=scope,
            type=discord.AppCommandType.chat_input,
        ):
            found.setdefault(command.name, command)

    return list(found.values())


def _leaves(command):
    """Every runnable command under a group, or the command itself."""
    if isinstance(command, app_commands.Group):
        for child in command.commands:
            yield from _leaves(child)
    else:
        yield command


def visible_commands(tree, user, guild=None) -> list:
    """
    The commands the user may run, as (area, command) pairs.

    Groups are expanded into their subcommands; the result is sorted
    by area, then by name.
    """
    admin = is_administrator(user)
    result = []

    for command in top_level_commands(tree, guild):
        if not _has_default_permissions(user, command):
            continue

        if command.name == "container" and not _container_allowed(user):
            continue

        area = AREAS.get(command.name, "Other")

        for leaf in _leaves(command):
            if leaf.qualified_name in ADMIN_ONLY and not admin:
                continue

            result.append((area, leaf))

    result.sort(
        key=lambda item: (
            AREA_ORDER.index(item[0]),
            item[1].qualified_name,
        )
    )

    return result


def _note(command) -> str:
    name = command.qualified_name
    root = getattr(command, "root_parent", None) or command
    required = getattr(root, "default_permissions", None)

    if name in ADMIN_ONLY or (required is not None and required.administrator):
        return "administrators"

    return ADMIN_NOTES.get(name, "")


def _line(command) -> str:
    line = f"`/{command.qualified_name}` — {command.description}"
    note = _note(command)

    if note:
        line += f" *({note})*"

    return line


# ----------------------------------------------------------------
# Building the embeds
# ----------------------------------------------------------------


def _chunks(lines: list[str]) -> list[str]:
    """Join lines into field values of at most FIELD_VALUE_LIMIT."""
    chunks = []
    current = ""

    for line in lines:
        line = line[:FIELD_VALUE_LIMIT]
        joined = f"{current}\n{line}" if current else line

        if len(joined) > FIELD_VALUE_LIMIT:
            chunks.append(current)
            current = line
        else:
            current = joined

    if current:
        chunks.append(current)

    return chunks


def overview_fields(visible: list, room: int) -> list[EmbedField]:
    """
    One field per area (split when it is too long), together at most
    `room` characters and FIELD_COUNT_LIMIT fields.
    """
    fields = []
    used = 0
    left_out = 0

    for area in AREA_ORDER:
        lines = [_line(command) for a, command in visible if a == area]

        for index, chunk in enumerate(_chunks(lines)):
            name = f"{AREA_EMOJI[area]} {area}"

            if index:
                name += " (continued)"

            if (
                len(fields) >= FIELD_COUNT_LIMIT - 1
                or used + len(name) + len(chunk) > room
            ):
                left_out += chunk.count("\n") + 1
                continue

            fields.append(EmbedField(name=name, value=chunk, inline=False))
            used += len(name) + len(chunk)

    if left_out:
        fields.append(
            EmbedField(
                name="More",
                value=f"{left_out} more not shown. Use `/help <command>`.",
                inline=False,
            )
        )

    return fields


def _embed_size(embed: discord.Embed) -> int:
    footer = getattr(embed, "footer", None)
    footer_text = getattr(footer, "text", None) or ""

    return (
        len(embed.title or "")
        + len(embed.description or "")
        + len(footer_text)
    )


def overview_embed(interaction: discord.Interaction, visible: list) -> discord.Embed:
    embed = create_embed(
        interaction=interaction,
        title=f"{theme.INFO_EMOJI} {theme.BOT_NAME} — Commands",
        description=(
            "The commands you can use. "
            "`/help <command>` shows one command with its options."
        ),
        color=theme.PRIMARY_COLOR,
    )

    room = EMBED_TOTAL_LIMIT - _embed_size(embed) - 100

    for field in overview_fields(visible, room):
        embed.add_field(name=field.name, value=field.value, inline=field.inline)

    if not visible:
        embed.description = "There are no commands you can use."

    return embed


def _option_text(parameter) -> str:
    text = parameter.description or "—"
    choices = getattr(parameter, "choices", None) or []

    if choices:
        text += "\nChoices: " + ", ".join(choice.name for choice in choices)

    if getattr(parameter, "autocomplete", False):
        text += "\nSuggestions appear while typing."

    return text[:FIELD_VALUE_LIMIT]


def detail_embed(interaction: discord.Interaction, command) -> discord.Embed:
    """One command with its options, or a group with its subcommands."""
    description = command.description or ""
    note = _note(command)

    if note:
        description += f" *({note})*"

    embed = create_embed(
        interaction=interaction,
        title=f"{theme.INFO_EMOJI} /{command.qualified_name}",
        description=description[:DETAIL_DESCRIPTION_LIMIT],
        color=theme.PRIMARY_COLOR,
    )

    room = EMBED_TOTAL_LIMIT - _embed_size(embed) - 100
    used = 0

    for parameter in list(getattr(command, "parameters", []))[:FIELD_COUNT_LIMIT]:
        required = "required" if parameter.required else "optional"
        name = f"`{parameter.display_name}` ({required})"
        value = _option_text(parameter)

        if used + len(name) + len(value) > room:
            break

        embed.add_field(name=name, value=value, inline=False)
        used += len(name) + len(value)

    if not getattr(command, "parameters", None):
        embed.add_field(name="Options", value="None.", inline=False)

    return embed


def find_visible(visible: list, name: str):
    """The visible command with this name, with or without a leading /."""
    wanted = " ".join(name.strip().lstrip("/").lower().split())

    for _area, command in visible:
        if command.qualified_name.lower() == wanted:
            return command

    return None


# ----------------------------------------------------------------
# Cog
# ----------------------------------------------------------------


class Help(commands.Cog):
    """The /help command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _visible(interaction: discord.Interaction) -> list:
        return visible_commands(
            interaction.client.tree,
            interaction.user,
            interaction.guild,
        )

    async def _command_names(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Names of the commands the user may run, for autocomplete."""
        wanted = current.strip().lstrip("/").lower()

        return [
            app_commands.Choice(
                name=f"/{command.qualified_name}",
                value=command.qualified_name,
            )
            for _area, command in self._visible(interaction)
            if wanted in command.qualified_name.lower()
        ][:25]

    @app_commands.command(
        name="help",
        description="List the commands you can use, or show one in detail.",
    )
    @app_commands.describe(command="Show the options of this command.")
    async def help_command(
        self,
        interaction: discord.Interaction,
        command: str | None = None,
    ):
        visible = self._visible(interaction)

        if command is None:
            embed = overview_embed(interaction, visible)
        else:
            found = find_visible(visible, command)

            if found is None:
                await interaction.response.send_message(
                    f"There is no command `/{command.strip().lstrip('/')}` "
                    "that you can use. Run `/help` for the list.",
                    ephemeral=True,
                )
                return

            embed = detail_embed(interaction, found)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        logger.info(
            "Help requested by %s%s",
            interaction.user,
            f" for /{command}" if command else "",
        )

    @help_command.autocomplete("command")
    async def _help_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._command_names(interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
