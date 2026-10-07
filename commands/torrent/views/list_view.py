"""Discord UI view for paginated and live-refreshing torrent lists."""
import asyncio
import discord
from services.qbittorrent_service import QBittorrentService
from utils.logger import logger
from utils.open_messages import ExpiringView
from commands.torrent.list_processing import (
    _build_pages,
    _filter_torrents,
    _sort_torrents,
)
class TorrentListView(ExpiringView):
    """Paginated Discord view for displaying torrent lists."""
    TORRENTS_PER_PAGE = 10
    REFRESH_INTERVAL = 10
    def __init__(
        self,
        interaction: discord.Interaction,
        pages: list[dict],
        filter_name: str,
        total_count: int,
        torrent_cog,
        selected_category: str | None = None,
        sort_name: str = "default",
    ):
        super().__init__(timeout=180)
        self.owner_id = interaction.user.id
        self.pages = pages
        self.filter_name = filter_name
        self.total_count = total_count
        self.torrent_cog = torrent_cog
        self.selected_category = selected_category
        self.sort_name = sort_name
        self.interaction = interaction
        self.current_page = 0
        self.total_pages = max(
            1,
            len(self.pages),
        )
        self.message: discord.Message | None = None
        self.refresh_task: asyncio.Task | None = None
        self.refresh_lock = asyncio.Lock()
        self.previous_button = discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.secondary,
        )
        self.page_button = discord.ui.Button(
            label=self._page_label(),
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.next_button = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
        )
        self.previous_button.callback = self._previous_page
        self.next_button.callback = self._next_page
        self.add_item(self.previous_button)
        self.add_item(self.page_button)
        self.add_item(self.next_button)
        self._update_buttons()
    def start_refresh(self) -> None:
        """Start the background torrent list refresh task."""
        if (
            self.refresh_task is None
            or self.refresh_task.done()
        ):
            self.refresh_task = asyncio.create_task(
                self._refresh_loop()
            )
            logger.debug(
                "[TorrentListView] Started automatic refresh "
                "(interval=%ss).",
                self.REFRESH_INTERVAL,
            )
    async def _refresh_loop(self) -> None:
        """Refresh the torrent list periodically until the view expires."""
        try:
            while not self.is_finished():
                await asyncio.sleep(
                    self.REFRESH_INTERVAL
                )
                if self.is_finished():
                    break
                try:
                    await self._refresh_list()
                except asyncio.CancelledError:
                    raise
                except discord.NotFound:
                    logger.info(
                        "[TorrentListView] Torrent list message "
                        "no longer exists. Stopping refresh."
                    )
                    self.stop()
                    break
                except discord.HTTPException as ex:
                    logger.warning(
                        "[TorrentListView] Failed to refresh "
                        "torrent list: %s",
                        ex,
                    )
                except Exception:
                    logger.exception(
                        "[TorrentListView] Unexpected error "
                        "during automatic refresh."
                    )
        except asyncio.CancelledError:
            logger.debug(
                "[TorrentListView] Automatic refresh task cancelled."
            )
            raise
    async def _refresh_list(self) -> None:
        """Fetch current qBittorrent data and refresh the list message."""
        if self.message is None:
            return
        async with self.refresh_lock:
            service = QBittorrentService()
            try:
                all_torrents = await service.get_torrents()
            finally:
                await service.close()
            torrents = [
                torrent
                for torrent in all_torrents
                if torrent.get("category")
                in self.torrent_cog.ALLOWED_CATEGORIES
            ]
            if self.selected_category is not None:
                torrents = [
                    torrent
                    for torrent in torrents
                    if torrent.get("category")
                    == self.selected_category
                ]
            torrents = _filter_torrents(
                torrents,
                self.filter_name,
                self.torrent_cog.ACTIVE_STATES,
                self.torrent_cog.SEEDING_STATES,
            )
            torrents = _sort_torrents(
                torrents,
                self.sort_name,
            )
            pages = _build_pages(
                torrents=torrents,
                selected_category=self.selected_category,
                allowed_categories=self.torrent_cog.ALLOWED_CATEGORIES,
                torrents_per_page=self.TORRENTS_PER_PAGE,
            )
            self.pages = pages
            self.total_count = len(torrents)
            self.total_pages = max(
                1,
                len(self.pages),
            )
            if self.pages:
                self.current_page = min(
                    self.current_page,
                    len(self.pages) - 1,
                )
            else:
                self.current_page = 0
            self._update_buttons()
            if self.pages:
                page = self.get_current_page()
                category = page["category"]
                page_torrents = page["torrents"]
            else:
                category = (
                    self.selected_category
                    or "All"
                )
                page_torrents = []
            embed = self.torrent_cog.build_list_embed(
                interaction=self.interaction,
                torrents=page_torrents,
                category=category,
                filter_name=self.filter_name,
                current_page=self.current_page,
                total_pages=self.total_pages,
                total_count=self.total_count,
            )
            await self.message.edit(
                embed=embed,
                view=self,
            )
    def _page_label(self) -> str:
        """Return the current page indicator."""
        return (
            f"{self.current_page + 1} / "
            f"{self.total_pages}"
        )
    def _update_buttons(self) -> None:
        """Update pagination button states."""
        self.previous_button.disabled = (
            self.current_page <= 0
            or not self.pages
        )
        self.next_button.disabled = (
            self.current_page
            >= self.total_pages - 1
            or not self.pages
        )
        self.page_button.label = self._page_label()
    def get_current_page(self) -> dict:
        """Return the currently displayed page."""
        if not self.pages:
            raise RuntimeError(
                "Torrent list contains no pages."
            )
        return self.pages[self.current_page]
    async def _render_page(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Build and edit the current pagination page."""
        page = self.get_current_page()
        embed = self.torrent_cog.build_list_embed(
            interaction=interaction,
            torrents=page["torrents"],
            category=page["category"],
            filter_name=self.filter_name,
            current_page=self.current_page,
            total_pages=self.total_pages,
            total_count=self.total_count,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )
    async def _previous_page(
        self,
        interaction: discord.Interaction,
    ):
        """Move to the previous page."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who requested this list "
                "can control the pagination.",
                ephemeral=True,
            )
            return
        if self.current_page > 0:
            self.current_page -= 1
        self._update_buttons()
        try:
            await self._render_page(
                interaction
            )
        except discord.HTTPException:
            logger.exception(
                "[TorrentListView] Failed to render previous page."
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to update the torrent list.",
                    ephemeral=True,
                )
        except Exception:
            logger.exception(
                "[TorrentListView] Unexpected error rendering "
                "previous page."
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An unexpected error occurred while "
                    "updating the torrent list.",
                    ephemeral=True,
                )
    async def _next_page(
        self,
        interaction: discord.Interaction,
    ):
        """Move to the next page."""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who requested this list "
                "can control the pagination.",
                ephemeral=True,
            )
            return
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        self._update_buttons()
        try:
            await self._render_page(
                interaction
            )
        except discord.HTTPException:
            logger.exception(
                "[TorrentListView] Failed to render next page."
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to update the torrent list.",
                    ephemeral=True,
                )
        except Exception:
            logger.exception(
                "[TorrentListView] Unexpected error rendering "
                "next page."
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An unexpected error occurred while "
                    "updating the torrent list.",
                    ephemeral=True,
                )
    async def on_timeout(self):
        """Disable pagination buttons after the view expires."""
        if (
            self.refresh_task is not None
            and not self.refresh_task.done()
        ):
            self.refresh_task.cancel()
        self.previous_button.disabled = True
        self.next_button.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(
                    view=self,
                )
        except discord.HTTPException:
            pass