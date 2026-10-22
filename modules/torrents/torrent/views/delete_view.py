"""Discord UI views for confirming torrent deletion."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from modules.torrents.qbittorrent_service import QBittorrentService
from utils.logger import logger
from utils.open_messages import ExpiringView

if TYPE_CHECKING:
    from modules.torrents.torrent.views.control_view import TorrentControlView


async def _require_administrator(
    interaction: discord.Interaction,
) -> bool:
    """
    Ensure the user may permanently delete downloaded files.

    Removing a torrent is open to whoever requested it, but deleting
    the files as well is limited to server administrators.
    """
    permissions = getattr(
        interaction.user,
        "guild_permissions",
        None,
    )

    if permissions is not None and permissions.administrator:
        return True

    await interaction.response.send_message(
        "Only a server administrator can delete downloaded files. "
        "You can still remove the torrent and keep its files.",
        ephemeral=True,
    )

    return False


class TorrentDeleteConfirmationView(ExpiringView):
    """First-stage torrent deletion options."""

    def __init__(
        self,
        parent_view: "TorrentControlView",
    ):
        super().__init__(timeout=60)

        self.parent_view = parent_view
        self.owner_id = parent_view.owner_id

        self.keep_files_button = discord.ui.Button(
            label="Remove torrent",
            style=discord.ButtonStyle.primary,
        )

        self.delete_files_button = discord.ui.Button(
            label="Remove torrent + files",
            style=discord.ButtonStyle.danger,
        )

        self.cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )

        self.keep_files_button.callback = self._keep_files
        self.delete_files_button.callback = self._delete_files
        self.cancel_button.callback = self._cancel

        self.add_item(self.keep_files_button)
        self.add_item(self.delete_files_button)
        self.add_item(self.cancel_button)

    async def _check_owner(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """Ensure only the original requester can confirm."""
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who requested this torrent "
            "information can use this confirmation.",
            ephemeral=True,
        )

        return False

    def _disable_all_buttons(self) -> None:
        """Disable all buttons in this view."""
        for item in self.children:
            item.disabled = True

    async def _keep_files(
        self,
        interaction: discord.Interaction,
    ):
        """Remove the torrent while keeping its downloaded files."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer(
            ephemeral=True,
        )

        service = QBittorrentService()

        try:
            torrent_hash = self.parent_view.torrent.get(
                "hash",
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            await service.delete_torrent(
                torrent_hash,
                delete_files=False,
            )

            self._disable_all_buttons()

            await interaction.edit_original_response(
                content=(
                    "Torrent removed from qBittorrent. "
                    "Downloaded files were kept."
                ),
                view=self,
            )

            self.parent_view.stop()

            if self.parent_view.message is not None:
                try:
                    await self.parent_view.message.edit(
                        view=None,
                    )
                except discord.HTTPException:
                    pass

        except Exception as ex:
            logger.exception(
                "[TorrentDeleteConfirmationView] "
                "Failed to delete torrent while keeping files: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to remove the torrent.",
                ephemeral=True,
            )

        finally:
            await service.close()

    async def _delete_files(
        self,
        interaction: discord.Interaction,
    ):
        """Open the second confirmation before deleting files."""
        if not await self._check_owner(
            interaction
        ):
            return

        if not await _require_administrator(
            interaction
        ):
            return

        confirmation = TorrentDeleteFilesConfirmationView(
            parent_view=self.parent_view,
        )

        torrent_name = self.parent_view.torrent.get(
            "name",
            "Unknown torrent",
        )

        await interaction.response.edit_message(
            content=(
                "**Permanently delete torrent and files?**\n\n"
                f"`{torrent_name}`\n\n"
                "This will remove the torrent from qBittorrent "
                "and permanently delete its downloaded files."
            ),
            view=confirmation,
        )

    async def _cancel(
        self,
        interaction: discord.Interaction,
    ):
        """Cancel the delete operation."""
        if not await self._check_owner(
            interaction
        ):
            return

        self._disable_all_buttons()

        await interaction.response.edit_message(
            content="Torrent deletion cancelled.",
            view=self,
        )

    async def on_timeout(self):
        """Disable confirmation buttons after timeout."""
        self._disable_all_buttons()


class TorrentDeleteFilesConfirmationView(ExpiringView):
    """Second-stage confirmation for permanently deleting torrent files."""

    def __init__(
        self,
        parent_view: "TorrentControlView",
    ):
        super().__init__(timeout=60)

        self.parent_view = parent_view
        self.owner_id = parent_view.owner_id

        self.confirm_button = discord.ui.Button(
            label="Delete files",
            style=discord.ButtonStyle.danger,
        )

        self.cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )

        self.confirm_button.callback = self._confirm
        self.cancel_button.callback = self._cancel

        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    async def _check_owner(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """Ensure only the original requester can confirm."""
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who requested this torrent "
            "information can use this confirmation.",
            ephemeral=True,
        )

        return False

    def _disable_all_buttons(self) -> None:
        """Disable all buttons in this view."""
        for item in self.children:
            item.disabled = True

    async def _confirm(
        self,
        interaction: discord.Interaction,
    ):
        """Remove the torrent and permanently delete its files."""
        if not await self._check_owner(
            interaction
        ):
            return

        if not await _require_administrator(
            interaction
        ):
            return

        await interaction.response.defer(
            ephemeral=True,
        )

        service = QBittorrentService()

        try:
            torrent_hash = self.parent_view.torrent.get(
                "hash",
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            await service.delete_torrent(
                torrent_hash,
                delete_files=True,
            )

            self._disable_all_buttons()

            torrent_name = self.parent_view.torrent.get(
                "name",
                "Unknown torrent",
            )

            await interaction.edit_original_response(
                content=(
                    f"Torrent `{torrent_name}` was removed from "
                    "qBittorrent and its downloaded files were deleted."
                ),
                view=self,
            )

            self.parent_view.stop()

            if self.parent_view.message is not None:
                try:
                    await self.parent_view.message.edit(
                        view=None,
                    )
                except discord.HTTPException:
                    pass

        except Exception as ex:
            logger.exception(
                "[TorrentDeleteFilesConfirmationView] "
                "Failed to delete torrent and files: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to remove the torrent and its files.",
                ephemeral=True,
            )

        finally:
            await service.close()

    async def _cancel(
        self,
        interaction: discord.Interaction,
    ):
        """Cancel permanent deletion."""
        if not await self._check_owner(
            interaction
        ):
            return

        self._disable_all_buttons()

        await interaction.response.edit_message(
            content="Torrent deletion cancelled.",
            view=self,
        )

    async def on_timeout(self):
        """Disable confirmation buttons after timeout."""
        self._disable_all_buttons()