"""The picker under the daily "new on another Jellyfin" alert."""

from __future__ import annotations

import discord

from commands.torrent.jellyfin_download import (
    JellyfinSourceView,
    existing_series_folder,
    find_missing_content,
)
from services.jellyfin_service import JellyfinService
from utils.logger import logger
from utils.open_messages import ExpiringView


class NewContentView(ExpiringView):
    """
    Lets anyone pick a series from the alert and copy what is missing.

    Each finding is a dict with own (the primary library's series),
    theirs (the other server's series) and server (its name). Picking
    one compares the two series again, so the offer is current, and
    posts the usual download message for the person who picked it.
    """

    # The next daily alert replaces this one.
    TIMEOUT = 23 * 60 * 60

    def __init__(self, bot, findings: list[dict]):
        super().__init__(timeout=self.TIMEOUT)

        self.bot = bot
        self.findings = findings[:25]

        self.select = discord.ui.Select(
            placeholder="Choose a series to copy what is missing…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=finding["label"][:100],
                    description=finding["summary"][:100] or None,
                    value=str(index),
                )
                for index, finding in enumerate(self.findings)
            ],
        )

        self.select.callback = self._choose
        self.add_item(self.select)

    @staticmethod
    def _match(server, item: dict) -> dict:
        """Describe a library item the way a title search does."""
        return {
            "server": server.name,
            "primary": server.primary,
            "url": server.url,
            "title": str(item.get("Name") or "Unknown title"),
            "year": item.get("ProductionYear"),
            "type": "Series",
            "item_id": str(item.get("Id") or ""),
            "path": str(item.get("Path") or ""),
        }

    async def _choose(self, interaction: discord.Interaction):
        finding = self.findings[int(self.select.values[0])]

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Download from Jellyfin",
                description=(
                    f"Comparing **{finding['label']}** with "
                    f"**{finding['server']}**."
                ),
                color=discord.Color.blurple(),
            ),
        )

        service = JellyfinService()
        primary_server = service.primary_server()
        source_server = service._get_server(finding["server"])

        if primary_server is None or source_server is None:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="Nothing to download",
                    description="That Jellyfin server is no longer configured.",
                    color=discord.Color.blurple(),
                ),
            )
            return

        primary = self._match(primary_server, finding["own"])
        source = self._match(source_server, finding["theirs"])

        found = await find_missing_content([primary, source], "Series")

        if found is None:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="Nothing to download",
                    description=(
                        f"Your library already has everything "
                        f"**{finding['server']}** has of "
                        f"**{finding['label']}**."
                    ),
                    color=discord.Color.green(),
                ),
            )
            return

        # Imported here because the search module imports this package.
        from commands.torrent.search import TorrentSearchMixin

        view = JellyfinSourceView(
            owner_id=interaction.user.id,
            query=primary["title"],
            category="Series",
            torrent_cog=self.bot.get_cog("Torrent"),
            match=found["source"],
            missing=found["missing"],
            own_episodes=found["own_episodes"],
            folder_name=existing_series_folder(
                found["primary"],
                JellyfinService.library_dir("Series"),
            ),
        )

        logger.info(
            "[NewContent] %s picked '%s' from %s.",
            interaction.user,
            finding["label"],
            finding["server"],
        )

        view.message = await interaction.edit_original_response(
            embed=TorrentSearchMixin._build_jellyfin_embed(
                [primary, source],
                found["source"],
                found["missing"],
            ),
            view=view,
        )
