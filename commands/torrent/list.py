"""Torrent list command implementation and pagination preparation."""
import discord
from commands.torrent.views import TorrentListView
from services.qbittorrent_service import QBittorrentService
from utils import theme
from utils.embeds import create_embed
from utils.logger import logger
from .list_processing import (
    _build_pages,
    _filter_torrents,
    _safe_float,
    _safe_int,
    _sort_torrents,
)
class TorrentListMixin:
    """Mixin providing torrent list functionality."""
    async def _list(
        self,
        interaction: discord.Interaction,
        category: str | None = None,
        filter_name: str = "active",
        sort_name: str = "default",
    ):
        """Retrieve and display a paginated torrent list."""
        logger.info(
            "[Torrent] List command started."
        )
        await interaction.response.defer()
        service = QBittorrentService()
        try:
            logger.info(
                "[Torrent] Requesting all torrents from qBittorrent."
            )
            all_torrents = await service.get_torrents()
            logger.info(
                "[Torrent] Received %s torrents.",
                len(all_torrents),
            )
        except Exception as ex:
            logger.exception(
                "[Torrent] Failed to retrieve torrent list: %s",
                ex,
            )
            await interaction.followup.send(
                "Unable to retrieve the torrent list from "
                "qBittorrent."
            )
            return
        finally:
            await service.close()
        # Only expose the categories configured for Aegis-Bot.
        torrents = [
            torrent
            for torrent in all_torrents
            if torrent.get("category")
            in self.ALLOWED_CATEGORIES
        ]
        logger.info(
            "[Torrent] %s torrents remain after managed-category "
            "filtering.",
            len(torrents),
        )
        # Apply the requested category locally.
        if category is not None:
            if category not in self.ALLOWED_CATEGORIES:
                await interaction.followup.send(
                    "That qBittorrent category is not "
                    "available through Aegis-Bot."
                )
                return
            torrents = [
                torrent
                for torrent in torrents
                if torrent.get("category") == category
            ]
            logger.info(
                "[Torrent] %s torrents remain after category "
                "filtering (category=%s).",
                len(torrents),
                category,
            )
        # Apply the requested state filter.
        torrents = _filter_torrents(
            torrents,
            filter_name,
            self.ACTIVE_STATES,
            self.SEEDING_STATES,
        )
        logger.info(
            "[Torrent] %s torrents remain after state filtering "
            "(filter=%s).",
            len(torrents),
            filter_name,
        )
        # Apply the requested sort.
        torrents = _sort_torrents(
            torrents,
            sort_name,
        )
        logger.info(
            "[Torrent] Torrents sorted using '%s'.",
            sort_name,
        )
        if not torrents:
            await self._send_empty_list(
                interaction,
                category,
                filter_name,
            )
            return
        pages = _build_pages(
            torrents=torrents,
            selected_category=category,
            allowed_categories=self.ALLOWED_CATEGORIES,
            torrents_per_page=TorrentListView.TORRENTS_PER_PAGE,
        )
        logger.info(
            "[Torrent] Built %s pages.",
            len(pages),
        )
        if not pages:
            await self._send_empty_list(
                interaction,
                category,
                filter_name,
            )
            return
        view = TorrentListView(
            interaction=interaction,
            pages=pages,
            filter_name=filter_name,
            total_count=len(torrents),
            torrent_cog=self,
            selected_category=category,
            sort_name=sort_name,
        )
        page = view.get_current_page()
        embed = self.build_list_embed(
            interaction=interaction,
            torrents=page["torrents"],
            category=page["category"],
            filter_name=filter_name,
            current_page=view.current_page,
            total_pages=view.total_pages,
            total_count=len(torrents),
        )
        message = await interaction.followup.send(
            embed=embed,
            view=view,
            wait=True,
        )
        view.message = message
        view.start_refresh()
        logger.info(
            "[Torrent] Torrent list requested by %s "
            "(category=%s, filter=%s, sort=%s, results=%s, pages=%s).",
            interaction.user,
            category or "all",
            filter_name,
            sort_name,
            len(torrents),
            len(pages),
        )
    async def _send_empty_list(
        self,
        interaction: discord.Interaction,
        category: str | None,
        filter_name: str,
    ):
        """Send a friendly response when no torrents match."""
        if category:
            scope = f"the **{category}** category"
        else:
            scope = "the Aegis-Bot managed categories"
        filter_text = {
            "active": "active torrents",
            "seeding": "seeding torrents",
            "all": "torrents",
        }.get(
            filter_name,
            "torrents",
        )
        embed = create_embed(
            interaction=interaction,
            title=(
                f"{theme.STORAGE_EMOJI} "
                "qBittorrent Torrents"
            ),
            description=(
                f"No {filter_text} found in {scope}."
            ),
        )
        await interaction.followup.send(
            embed=embed,
        )
        logger.info(
            "[Torrent] Empty torrent list requested by %s "
            "(category=%s, filter=%s).",
            interaction.user,
            category or "all",
            filter_name,
        )
    def build_list_embed(
        self,
        interaction: discord.Interaction,
        torrents: list[dict],
        category: str,
        filter_name: str,
        current_page: int,
        total_pages: int,
        total_count: int,
    ) -> discord.Embed:
        """Build the embed for one category page."""
        title = (
            f"{theme.STORAGE_EMOJI} "
            f"qBittorrent — {category}"
        )
        description = self._build_category_list(
            torrents
        )
        embed = create_embed(
            interaction=interaction,
            title=title,
            description=description,
        )
        filter_labels = {
            "active": "Active",
            "seeding": "Seeding",
            "all": "All",
        }
        filter_text = filter_labels.get(
            filter_name,
            "Active",
        )
        embed.set_footer(
            text=(
                f"{filter_text} • "
                f"{total_count} "
                f"{'torrent' if total_count == 1 else 'torrents'} "
                f"• Page {current_page + 1}/{total_pages}"
            )
        )
        return embed
    def _build_category_list(
        self,
        torrents: list[dict],
    ) -> str:
        """Build a compact list for one category."""
        return "\n\n".join(
            self._format_list_torrent(torrent)
            for torrent in torrents
        )
    def _format_list_torrent(
        self,
        torrent: dict,
    ) -> str:
        """Format one torrent for the compact list."""
        name = self._truncate_name(
            torrent.get(
                "name",
                "Unknown torrent",
            )
        )
        progress = _safe_float(
            torrent.get(
                "progress",
                0,
            )
        )
        state = self._format_state(
            torrent.get(
                "state",
                "unknown",
            )
        )
        progress_bar = self._format_progress_bar(
            progress,
            width=12,
        )
        download_speed = self._format_speed(
            torrent.get(
                "dlspeed",
                0,
            )
        )
        upload_speed = self._format_speed(
            torrent.get(
                "upspeed",
                0,
            )
        )
        remaining = self._format_bytes(
            torrent.get(
                "amount_left",
                0,
            )
        )
        eta = self._format_eta(
            torrent.get(
                "eta",
                0,
            )
        )
        ratio = _safe_float(
            torrent.get(
                "ratio",
                0,
            )
        )
        seeds = _safe_int(
            torrent.get(
                "num_seeds",
                torrent.get(
                    "seeds",
                    0,
                ),
            )
        )
        peers = _safe_int(
            torrent.get(
                "num_leechs",
                torrent.get(
                    "peers",
                    0,
                ),
            )
        )
        added = self._format_added_time(
            torrent.get(
                "added_on",
                0,
            )
        )
        return (
            f"**{name}**\n"
            f"{progress_bar} • {state}\n"
            f"↓ {download_speed} • "
            f"↑ {upload_speed} • "
            f"ETA {eta}\n"
            f"{remaining} left • "
            f"Seeds {seeds} • "
            f"Peers {peers} • "
            f"Ratio {ratio:.2f}\n"
            f"Added {added}"
        )