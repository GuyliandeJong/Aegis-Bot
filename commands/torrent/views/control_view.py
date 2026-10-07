"""Discord UI view for controlling individual torrents."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from services.qbittorrent_service import QBittorrentService
from utils.logger import logger

from .delete_view import TorrentDeleteConfirmationView

if TYPE_CHECKING:
    from commands.torrent.torrent_cog import TorrentCog


class TorrentControlView(discord.ui.View):
    """Controls for an individual torrent."""

    REFRESH_INTERVAL = 10

    def __init__(
        self,
        owner_id: int,
        torrent_hash: str,
        torrent_name: str,
        torrent: dict | None = None,
        torrent_cog=None,
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.torrent_cog = torrent_cog

        if torrent is not None:
            self.torrent = torrent.copy()
            self.torrent.setdefault(
                "hash",
                torrent_hash,
            )
            self.torrent.setdefault(
                "name",
                torrent_name,
            )
        else:
            self.torrent = {
                "hash": torrent_hash,
                "name": torrent_name,
            }

        self.message: discord.Message | None = None
        self.refresh_task: asyncio.Task | None = None
        self.refresh_lock = asyncio.Lock()

        self.pause_button = discord.ui.Button(
            label="Pause",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.resume_button = discord.ui.Button(
            label="Resume",
            style=discord.ButtonStyle.success,
            row=0,
        )
        self.delete_button = discord.ui.Button(
            label="Delete",
            style=discord.ButtonStyle.danger,
            row=0,
        )
        self.recheck_button = discord.ui.Button(
            label="Recheck",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        self.reannounce_button = discord.ui.Button(
            label="Reannounce",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        self.increase_priority_button = discord.ui.Button(
            label="⬆ Raise Priority",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.decrease_priority_button = discord.ui.Button(
            label="⬇ Lower Priority",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.top_priority_button = discord.ui.Button(
            label="⏫ Top Priority",
            style=discord.ButtonStyle.success,
            row=3,
        )
        self.bottom_priority_button = discord.ui.Button(
            label="⏬ Bottom Priority",
            style=discord.ButtonStyle.secondary,
            row=3,
        )

        self.pause_button.callback = self._pause
        self.resume_button.callback = self._resume
        self.delete_button.callback = self._delete
        self.recheck_button.callback = self._recheck
        self.reannounce_button.callback = self._reannounce
        self.increase_priority_button.callback = (
            self._increase_priority
        )
        self.decrease_priority_button.callback = (
            self._decrease_priority
        )
        self.top_priority_button.callback = (
            self._top_priority
        )
        self.bottom_priority_button.callback = (
            self._bottom_priority
        )

        self.add_item(self.pause_button)
        self.add_item(self.resume_button)
        self.add_item(self.delete_button)
        self.add_item(self.recheck_button)
        self.add_item(self.reannounce_button)
        self.add_item(self.increase_priority_button)
        self.add_item(self.decrease_priority_button)
        self.add_item(self.top_priority_button)
        self.add_item(self.bottom_priority_button)

        self._update_buttons()

    @staticmethod
    def _is_completed(torrent: dict) -> bool:
        """Return True when qBittorrent reports the torrent as complete."""
        state = str(
            torrent.get(
                "state",
                "",
            )
        ).lower()

        try:
            progress = float(
                torrent.get(
                    "progress",
                    0,
                )
            )
        except (TypeError, ValueError):
            progress = 0

        return (
            state == "completed"
            or progress >= 1.0
        )

    def start_refresh(self) -> None:
        """Start the background torrent information refresh task."""
        if (
            self.refresh_task is None
            or self.refresh_task.done()
        ):
            self.refresh_task = asyncio.create_task(
                self._refresh_loop()
            )

            logger.debug(
                "[TorrentControlView] Started automatic refresh "
                "(interval=%ss).",
                self.REFRESH_INTERVAL,
            )

    async def _refresh_loop(self) -> None:
        """Refresh torrent information periodically."""
        try:
            while not self.is_finished():
                await asyncio.sleep(
                    self.REFRESH_INTERVAL
                )

                if self.is_finished():
                    break

                try:
                    completed = await self._background_refresh()

                    if completed:
                        logger.info(
                            "[TorrentControlView] Torrent completed. "
                            "Final refresh performed; stopping automatic "
                            "refresh."
                        )
                        break

                except asyncio.CancelledError:
                    raise

                except discord.NotFound:
                    logger.info(
                        "[TorrentControlView] Torrent information "
                        "message no longer exists. Stopping refresh."
                    )
                    self.stop()
                    break

                except discord.HTTPException as ex:
                    logger.warning(
                        "[TorrentControlView] Failed to refresh "
                        "torrent information: %s",
                        ex,
                    )

                except Exception:
                    logger.exception(
                        "[TorrentControlView] Unexpected error during "
                        "automatic refresh."
                    )

        except asyncio.CancelledError:
            logger.debug(
                "[TorrentControlView] Automatic refresh task cancelled."
            )
            raise

    async def _background_refresh(self) -> bool:
        """
        Fetch the latest torrent information and update the message.

        Returns:
            True when the torrent is complete after this refresh.
        """
        if self.message is None:
            return False

        async with self.refresh_lock:
            service = QBittorrentService()

            try:
                torrent_hash = self.torrent.get(
                    "hash"
                )

                if not torrent_hash:
                    logger.warning(
                        "[TorrentControlView] Cannot refresh: "
                        "torrent hash is missing."
                    )
                    return False

                torrents = await service.get_torrent_info(
                    torrent_hash
                )

                if not torrents:
                    logger.info(
                        "[TorrentControlView] Torrent no longer exists. "
                        "Stopping refresh."
                    )
                    self.stop()
                    return True

                self.torrent = torrents[0].copy()

                completed = self._is_completed(
                    self.torrent
                )

                self._update_buttons()

                cog = self.torrent_cog

                if cog is None:
                    logger.warning(
                        "[TorrentControlView] Torrent cog is unavailable; "
                        "cannot rebuild the information embed."
                    )
                    return completed

                embed = cog._build_info_embed(
                    interaction=None,
                    torrent=self.torrent,
                )

                await self.message.edit(
                    embed=embed,
                    view=self,
                )

                return completed

            finally:
                await service.close()

    def _update_buttons(self) -> None:
        """Update control button states based on torrent state."""
        state = str(
            self.torrent.get(
                "state",
                "",
            )
        ).lower()

        stopped_states = {
            "stoppedup",
            "stoppeddl",
            "stopped",
        }

        paused_states = {
            "pausedup",
            "pauseddl",
            "paused",
        }

        checking_states = {
            "checkingdl",
            "checkingup",
            "checkingresumedata",
            "checkingresumedl",
            "checkingresumeup",
        }

        running_states = {
            "downloading",
            "stalleddl",
            "forceddl",
            "queueddl",
            "checkingdl",
            "checkingresumedl",
            "moving",
            "uploading",
            "stalledup",
            "forcedup",
            "queuedup",
            "checkingup",
            "checkingresumeup",
            "allocating",
        }

        if not state:
            self.pause_button.disabled = False
            self.resume_button.disabled = False

        elif (
            state in stopped_states
            or state in paused_states
        ):
            self.pause_button.disabled = True
            self.resume_button.disabled = False

        elif state in running_states:
            self.pause_button.disabled = False
            self.resume_button.disabled = True

        else:
            self.pause_button.disabled = False
            self.resume_button.disabled = False

        self.recheck_button.disabled = (
            state in checking_states
        )

        self.reannounce_button.disabled = (
            state in checking_states
        )

        priority_disabled = (
            state in {
                "uploading",
                "forcedup",
                "stalledup",
                "queuedup",
                "pausedup",
                "stoppedup",
                "completed",
                "checkingup",
                "checkingresumedata",
                "checkingresumedl",
                "checkingresumeup",
                "moving",
            }
        )

        self.increase_priority_button.disabled = (
            priority_disabled
        )

        self.decrease_priority_button.disabled = (
            priority_disabled
        )

        self.top_priority_button.disabled = (
            priority_disabled
        )

        self.bottom_priority_button.disabled = (
            priority_disabled
        )

    async def _check_owner(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """Ensure only the original requester can use the controls."""
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who requested this torrent "
            "information can control it.",
            ephemeral=True,
        )

        return False

    def _get_torrent_cog(
        self,
        interaction: discord.Interaction,
    ):
        """Return the loaded Torrent Cog from the Discord bot."""
        cog = interaction.client.get_cog(
            "Torrent"
        )

        if cog is None:
            raise RuntimeError(
                "The Torrent cog is not currently loaded."
            )

        return cog

    async def _refresh_info(
        self,
        interaction: discord.Interaction,
        wait_for_state_change: bool = False,
    ) -> None:
        """Refresh the torrent information message."""
        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            previous_state = str(
                self.torrent.get(
                    "state",
                    "",
                )
            ).lower()

            updated = None

            for attempt in range(10):
                updated_torrents = await service.get_torrent_info(
                    torrent_hash
                )

                if not updated_torrents:
                    raise RuntimeError(
                        "Torrent no longer exists."
                    )

                candidate = updated_torrents[0]

                current_state = str(
                    candidate.get(
                        "state",
                        "",
                    )
                ).lower()

                updated = candidate

                if (
                    not wait_for_state_change
                    or current_state != previous_state
                ):
                    break

                if attempt < 9:
                    await asyncio.sleep(0.5)

            self.torrent = updated.copy()

            self._update_buttons()

            cog = self._get_torrent_cog(
                interaction
            )

            embed = cog._build_info_embed(
                interaction=interaction,
                torrent=self.torrent,
            )

            await interaction.edit_original_response(
                embed=embed,
                view=self,
            )

        finally:
            await service.close()

    async def _pause(
        self,
        interaction: discord.Interaction,
    ):
        """Pause the selected torrent."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            await service.pause_torrent(
                torrent_hash
            )

            await self._refresh_info(
                interaction,
                wait_for_state_change=True,
            )

            await interaction.followup.send(
                "Torrent paused.",
                ephemeral=True,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to pause "
                "torrent: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to pause the torrent.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to pause the torrent.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _resume(
        self,
        interaction: discord.Interaction,
    ):
        """Resume the selected torrent."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            await service.resume_torrent(
                torrent_hash
            )

            await self._refresh_info(
                interaction,
                wait_for_state_change=True,
            )

            await interaction.followup.send(
                "Torrent resumed.",
                ephemeral=True,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to resume "
                "torrent: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to resume the torrent.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to resume the torrent.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _recheck(
        self,
        interaction: discord.Interaction,
    ):
        """Force a recheck of the selected torrent."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            logger.info(
                "[TorrentControlView] Rechecking torrent %s.",
                torrent_hash,
            )

            await service.recheck_torrent(
                torrent_hash
            )

            await self._refresh_info(
                interaction
            )

            await interaction.followup.send(
                "Torrent recheck started.",
                ephemeral=True,
            )

            logger.info(
                "[TorrentControlView] Recheck started "
                "for '%s' (hash=%s).",
                self.torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent_hash,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to recheck "
                "torrent: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to recheck the torrent.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to recheck the torrent.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _reannounce(
        self,
        interaction: discord.Interaction,
    ):
        """Force a tracker reannounce for the selected torrent."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            logger.info(
                "[TorrentControlView] Reannouncing torrent %s.",
                torrent_hash,
            )

            await service.reannounce_torrent(
                torrent_hash
            )

            await self._refresh_info(
                interaction
            )

            await interaction.followup.send(
                "Torrent reannounce requested.",
                ephemeral=True,
            )

            logger.info(
                "[TorrentControlView] Reannounce requested "
                "for '%s' (hash=%s).",
                self.torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent_hash,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to reannounce "
                "torrent: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to reannounce the torrent.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to reannounce the torrent.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _increase_priority(
        self,
        interaction: discord.Interaction,
    ):
        """Increase the selected torrent's queue priority."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            logger.info(
                "[TorrentControlView] Increasing priority "
                "for torrent %s.",
                torrent_hash,
            )

            await service.increase_priority(
                torrent_hash
            )

            await self._refresh_info(
                interaction
            )

            await interaction.followup.send(
                "Torrent priority increased.",
                ephemeral=True,
            )

            logger.info(
                "[TorrentControlView] Priority increased "
                "for '%s' (hash=%s).",
                self.torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent_hash,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to increase "
                "torrent priority: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to increase the torrent priority.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to increase the torrent priority.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _decrease_priority(
        self,
        interaction: discord.Interaction,
    ):
        """Decrease the selected torrent's queue priority."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            logger.info(
                "[TorrentControlView] Decreasing priority "
                "for torrent %s.",
                torrent_hash,
            )

            await service.decrease_priority(
                torrent_hash
            )

            await self._refresh_info(
                interaction
            )

            await interaction.followup.send(
                "Torrent priority decreased.",
                ephemeral=True,
            )

            logger.info(
                "[TorrentControlView] Priority decreased "
                "for '%s' (hash=%s).",
                self.torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent_hash,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to decrease "
                "torrent priority: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to decrease the torrent priority.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to decrease the torrent priority.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _top_priority(
        self,
        interaction: discord.Interaction,
    ):
        """Move the selected torrent to the top of the queue."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            logger.info(
                "[TorrentControlView] Moving torrent %s "
                "to top priority.",
                torrent_hash,
            )

            await service.top_priority(
                torrent_hash
            )

            await self._refresh_info(
                interaction
            )

            await interaction.followup.send(
                "Torrent moved to the top of the queue.",
                ephemeral=True,
            )

            logger.info(
                "[TorrentControlView] Torrent moved to top priority "
                "for '%s' (hash=%s).",
                self.torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent_hash,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to move torrent "
                "to top priority: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to move the torrent to the top "
                    "of the queue.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to move the torrent to the top "
                    "of the queue.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _bottom_priority(
        self,
        interaction: discord.Interaction,
    ):
        """Move the selected torrent to the bottom of the queue."""
        if not await self._check_owner(
            interaction
        ):
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            torrent_hash = self.torrent.get(
                "hash"
            )

            if not torrent_hash:
                raise RuntimeError(
                    "Torrent hash is missing."
                )

            logger.info(
                "[TorrentControlView] Moving torrent %s "
                "to bottom priority.",
                torrent_hash,
            )

            await service.bottom_priority(
                torrent_hash
            )

            await self._refresh_info(
                interaction
            )

            await interaction.followup.send(
                "Torrent moved to the bottom of the queue.",
                ephemeral=True,
            )

            logger.info(
                "[TorrentControlView] Torrent moved to bottom priority "
                "for '%s' (hash=%s).",
                self.torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent_hash,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentControlView] Failed to move torrent "
                "to bottom priority: %s",
                ex,
            )

            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Unable to move the torrent to the bottom "
                    "of the queue.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Unable to move the torrent to the bottom "
                    "of the queue.",
                    ephemeral=True,
                )

        finally:
            await service.close()

    async def _delete(
        self,
        interaction: discord.Interaction,
    ):
        """Open the torrent deletion options."""
        if not await self._check_owner(
            interaction
        ):
            return

        confirmation = TorrentDeleteConfirmationView(
            parent_view=self,
        )

        await interaction.response.send_message(
            "What would you like to do with this torrent?",
            view=confirmation,
            ephemeral=True,
        )

    async def on_timeout(self):
        """Disable controls after the view expires."""
        if self.refresh_task is not None:
            self.refresh_task.cancel()

        for item in self.children:
            item.disabled = True

        try:
            if self.message is not None:
                await self.message.edit(
                    view=self,
                )

        except discord.HTTPException:
            pass