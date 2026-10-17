"""
/jellyfin movies: the movies on all Jellyfin servers, with watched state.

    /jellyfin movies [show] [search]

show: All, Not watched (on no server), Watched (on any server), or
Not in my library (only on the other servers). Watched state is that
of JELLYFIN_<n>_USER on each server. The reply is private.
"""

import math

import discord
from discord import app_commands
from discord.ext import commands

from services import movie_library
from services.jellyfin_service import JellyfinService
from utils import theme
from utils.embeds import create_embed
from utils.logger import logger
from utils.open_messages import ExpiringView


MOVIE_EMOJI = "🎬"
PAGE_SIZE = 20
VIEW_TIMEOUT = 300

WATCHED = "✅"
NOT_WATCHED = "⚪"
UNKNOWN = "▫️"


def _server_mark(state) -> str:
    return {True: WATCHED, False: NOT_WATCHED}.get(state, UNKNOWN)


def _line(movie: dict, server_order: list[str]) -> str:
    title = discord.utils.escape_markdown(movie["title"])
    year = f" ({movie['year']})" if movie["year"] else ""
    places = " · ".join(
        f"{_server_mark(movie['servers'][name])} {name}"
        for name in server_order
        if name in movie["servers"]
    )
    return f"**{title}**{year} — {places}"


def movies_embed(
    interaction: discord.Interaction,
    shown: list[dict],
    total: int,
    show: str,
    search: str,
    server_order: list[str],
    users: dict[str, str],
    failed: list[str],
    page: int,
) -> tuple[discord.Embed, int, int]:
    """One page of the list. Returns (embed, page, total_pages)."""
    total_pages = max(1, math.ceil(len(shown) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    chunk = shown[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    watched = sum(1 for m in shown if movie_library.watched_anywhere(m))
    parts = [f"**{len(shown)}** movies"]

    if users:
        parts.append(f"**{watched}** watched")
        parts.append(f"**{len(shown) - watched}** not watched")

    lines = [" • ".join(parts)]

    if search:
        lines.append(f"Matching `{search}`, out of {total}.")

    for name in failed:
        lines.append(f"{theme.WARNING_EMOJI} {name} did not answer; its movies are missing.")

    lines.append("")

    if chunk:
        lines.extend(_line(movie, server_order) for movie in chunk)
    else:
        lines.append("No movies match.")

    legend = f"{WATCHED} watched · {NOT_WATCHED} not watched"

    if any(name not in users for name in server_order):
        legend += f" · {UNKNOWN} watched state not set up"

    lines += ["", legend]

    embed = create_embed(
        interaction=interaction,
        title=f"{MOVIE_EMOJI} Jellyfin — Movies",
        description="\n".join(lines),
    )

    footer = [
        movie_library.FILTERS.get(show, "All"),
        f"Page {page + 1}/{total_pages}",
    ]

    if users:
        footer.append(
            "Watched by " + ", ".join(f"{user} on {name}" for name, user in users.items())
        )

    embed.set_footer(text=" • ".join(footer))
    return embed, page, total_pages


class MoviesView(ExpiringView):
    """Previous / page / next, for the person who ran the command."""

    def __init__(self, user_id: int, render):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.user_id = user_id
        self.render_page = render
        self.page = 0
        self.total_pages = 1
        self.interaction: discord.Interaction | None = None

        self.previous_button = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary)
        self.page_button = discord.ui.Button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
        self.next_button = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)

        self.previous_button.callback = self._previous
        self.next_button.callback = self._next

        for button in (self.previous_button, self.page_button, self.next_button):
            self.add_item(button)

    def render(self, interaction: discord.Interaction) -> discord.Embed:
        embed, self.page, self.total_pages = self.render_page(interaction, self.page)
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1
        self.page_button.label = f"{self.page + 1} / {self.total_pages}"
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who ran the command can page through this list.",
                ephemeral=True,
            )
            return False

        return True

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=self.render(interaction), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=self.render(interaction), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass


class JellyfinCog(commands.Cog):
    """The /jellyfin command group."""

    jellyfin = app_commands.Group(
        name="jellyfin",
        description="Browse the Jellyfin libraries.",
    )

    @jellyfin.command(
        name="movies",
        description="Movies on all Jellyfin servers, and whether they are watched.",
    )
    @app_commands.describe(
        show="Which movies to list.",
        search="Only titles containing these words.",
    )
    @app_commands.choices(
        show=[app_commands.Choice(name=label, value=value) for value, label in movie_library.FILTERS.items()]
    )
    async def movies(
        self,
        interaction: discord.Interaction,
        show: str = "all",
        search: str = "",
    ):
        await interaction.response.defer(ephemeral=True)

        service = JellyfinService()

        if not service.servers:
            await interaction.followup.send(
                "No Jellyfin server is configured (JELLYFIN_1_URL and JELLYFIN_1_API_KEY).",
                ephemeral=True,
            )
            return

        try:
            movies, answered, failed = await movie_library.load(service)
        except Exception:
            logger.exception("[Jellyfin] Listing movies failed.")
            await interaction.followup.send("Listing the movies failed. Check the bot log.", ephemeral=True)
            return
        finally:
            await service.close()

        if not answered:
            await interaction.followup.send(
                "No Jellyfin server answered: " + ", ".join(failed) + ".",
                ephemeral=True,
            )
            return

        primary = service.primary_server()
        primary_name = primary.name if primary else answered[0]
        shown = movie_library.apply(movies, show, search, primary_name)
        # Servers whose watched state is shown: a user is set and found.
        users = {
            server.name: server.user
            for server in service.servers
            if server.user and server.name in answered and any(
                m["servers"].get(server.name) is not None for m in movies
            )
        }

        def render(page_interaction, page):
            return movies_embed(
                page_interaction, shown, len(movies), show, search,
                answered, users, failed, page,
            )

        view = MoviesView(interaction.user.id, render)
        message = await interaction.followup.send(
            embed=view.render(interaction),
            view=view,
            ephemeral=True,
            wait=True,
        )
        view.interaction = interaction
        view.message = message


async def setup(bot):
    await bot.add_cog(JellyfinCog())
