"""Discord slash command group for qBittorrent."""

import discord
from discord import app_commands
from discord.ext import commands

from commands.torrent.add import TorrentAddMixin
from commands.torrent.cleanup import TorrentCleanupMixin
from commands.torrent.constants import (
    ACTIVE_STATES,
    ALLOWED_CATEGORIES,
    SEEDING_STATES,
)
from commands.torrent.formatting import TorrentFormattingMixin
from commands.torrent.info import TorrentInfoMixin
from commands.torrent.list import TorrentListMixin
from commands.torrent.search import TorrentSearchMixin
from commands.torrent.speed import TorrentSpeedMixin
from commands.torrent.status import TorrentStatusMixin
from commands.torrent.watchlist import TorrentWatchlistMixin


async def torrent_info_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Provide autocomplete suggestions for /torrent info."""
    cog = interaction.client.get_cog("Torrent")

    if cog is None:
        return []

    return await cog._autocomplete_info_query(
        interaction,
        current,
    )


class Torrent(
    TorrentFormattingMixin,
    TorrentListMixin,
    TorrentInfoMixin,
    TorrentStatusMixin,
    TorrentAddMixin,
    TorrentSearchMixin,
    TorrentWatchlistMixin,
    TorrentCleanupMixin,
    TorrentSpeedMixin,
    commands.Cog,
):
    """
    Slash command group for qBittorrent.

    Available commands:
        /torrent status
        /torrent list
        /torrent info
        /torrent add
        /torrent search
        /torrent watchlist
        /torrent cleanup (administrators)
        /torrent speed (changing it: administrators)
    """

    ALLOWED_CATEGORIES = ALLOWED_CATEGORIES
    ACTIVE_STATES = ACTIVE_STATES
    SEEDING_STATES = SEEDING_STATES

    torrent_group = app_commands.Group(
        name="torrent",
        description="Manage and view qBittorrent torrents.",
    )

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    @torrent_group.command(
        name="status",
        description="Display qBittorrent server and transfer status.",
    )
    async def torrent_status(
        self,
        interaction: discord.Interaction,
    ):
        await self._status(interaction)

    @torrent_group.command(
        name="list",
        description="List torrents.",
    )
    @app_commands.describe(
        category="Optional qBittorrent category.",
        filter="Which torrent states to show.",
        sort="How to sort the torrents.",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(
                name="Movies",
                value="Movies",
            ),
            app_commands.Choice(
                name="Music",
                value="Music",
            ),
            app_commands.Choice(
                name="Series",
                value="Series",
            ),
            app_commands.Choice(
                name="PC",
                value="PC",
            ),
        ],
        filter=[
            app_commands.Choice(
                name="Active",
                value="active",
            ),
            app_commands.Choice(
                name="Seeding",
                value="seeding",
            ),
            app_commands.Choice(
                name="All",
                value="all",
            ),
        ],
        sort=[
            app_commands.Choice(
                name="Default",
                value="default",
            ),
            app_commands.Choice(
                name="Name — A-Z",
                value="name_asc",
            ),
            app_commands.Choice(
                name="Name — Z-A",
                value="name_desc",
            ),
            app_commands.Choice(
                name="Size — Largest",
                value="size_desc",
            ),
            app_commands.Choice(
                name="Size — Smallest",
                value="size_asc",
            ),
            app_commands.Choice(
                name="Progress — Highest",
                value="progress_desc",
            ),
            app_commands.Choice(
                name="Progress — Lowest",
                value="progress_asc",
            ),
            app_commands.Choice(
                name="Download Speed — Highest",
                value="download_speed_desc",
            ),
            app_commands.Choice(
                name="Download Speed — Lowest",
                value="download_speed_asc",
            ),
            app_commands.Choice(
                name="Upload Speed — Highest",
                value="upload_speed_desc",
            ),
            app_commands.Choice(
                name="Upload Speed — Lowest",
                value="upload_speed_asc",
            ),
        ],
    )
    async def torrent_list(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str] | None = None,
        filter: app_commands.Choice[str] | None = None,
        sort: app_commands.Choice[str] | None = None,
    ):
        category_name = (
            category.value
            if category is not None
            else None
        )

        if filter is not None:
            selected_filter = filter.value
        elif category is not None:
            selected_filter = "all"
        else:
            selected_filter = "active"

        selected_sort = (
            sort.value
            if sort is not None
            else "default"
        )

        await self._list(
            interaction,
            category=category_name,
            filter_name=selected_filter,
            sort_name=selected_sort,
        )

    @torrent_group.command(
        name="info",
        description="Display detailed information for a torrent.",
    )
    @app_commands.describe(
        query="Torrent name or part of a torrent name.",
        category="Optional qBittorrent category.",
    )
    @app_commands.autocomplete(
        query=torrent_info_autocomplete,
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(
                name="Movies",
                value="Movies",
            ),
            app_commands.Choice(
                name="Music",
                value="Music",
            ),
            app_commands.Choice(
                name="Series",
                value="Series",
            ),
            app_commands.Choice(
                name="PC",
                value="PC",
            ),
        ],
    )
    async def torrent_info(
        self,
        interaction: discord.Interaction,
        query: str,
        category: app_commands.Choice[str] | None = None,
    ):
        category_name = (
            category.value
            if category is not None
            else None
        )

        await self._info(
            interaction,
            category=category_name,
            query=query,
        )

    @torrent_group.command(
        name="add",
        description="Add a magnet link to qBittorrent.",
    )
    @app_commands.describe(
        magnet="BitTorrent magnet link.",
        category="qBittorrent category to download into.",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(
                name="Movies",
                value="Movies",
            ),
            app_commands.Choice(
                name="Music",
                value="Music",
            ),
            app_commands.Choice(
                name="Series",
                value="Series",
            ),
            app_commands.Choice(
                name="PC",
                value="PC",
            ),
        ],
    )
    async def torrent_add(
        self,
        interaction: discord.Interaction,
        magnet: str,
        category: app_commands.Choice[str],
    ):
        await self._add(
            interaction,
            category=category.value,
            magnet=magnet,
        )

    @torrent_group.command(
        name="search",
        description="Search Jackett and add a selected torrent.",
    )
    @app_commands.describe(
        query="Title or release to search for.",
        category="qBittorrent category to download into.",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(
                name="Movies",
                value="Movies",
            ),
            app_commands.Choice(
                name="Music",
                value="Music",
            ),
            app_commands.Choice(
                name="Series",
                value="Series",
            ),
            app_commands.Choice(
                name="PC",
                value="PC",
            ),
        ],
    )
    async def torrent_search(
        self,
        interaction: discord.Interaction,
        query: str,
        category: app_commands.Choice[str],
    ):
        await self._search(
            interaction,
            query=query,
            category=category.value,
        )

    @torrent_group.command(
        name="watchlist",
        description="Show or edit the titles you are waiting for.",
    )
    async def torrent_watchlist(
        self,
        interaction: discord.Interaction,
    ):
        await self._watchlist(interaction)

    @torrent_group.command(
        name="cleanup",
        description="Finished torrents by seeding; remove them (admins).",
    )
    async def torrent_cleanup(
        self,
        interaction: discord.Interaction,
    ):
        await self._cleanup(interaction)

    @torrent_group.command(
        name="speed",
        description="Show qBittorrent's speed, or switch normal and slow.",
    )
    @app_commands.describe(
        mode="Leave empty to see the current speed (switching: admins).",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name="Normal speed",
                value="normal",
            ),
            app_commands.Choice(
                name="Slow (alternative limits)",
                value="slow",
            ),
        ],
    )
    async def torrent_speed(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str] | None = None,
    ):
        await self._speed(
            interaction,
            mode.value if mode is not None else None,
        )


async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        Torrent(bot)
    )