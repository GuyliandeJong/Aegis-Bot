"""Torrent add command implementation."""

import asyncio

import discord

from modules.torrents.torrent.approval import (
    approval_required,
    ask_approval,
    is_admin,
)
from modules.torrents.torrent.views import TorrentControlView
from modules.torrents.qbittorrent_service import QBittorrentService
from modules.torrents.torrent_requesters import remember
from utils.formatting import format_bytes
from utils.logger import logger


class TorrentAddMixin:
    """Mixin providing torrent adding."""

    async def _add(
        self,
        interaction: discord.Interaction,
        category: str | None,
        source_url: str | None = None,
        magnet: str | None = None,
        requester=None,
        approved: bool = False,
    ):
        """
        Add a torrent source to qBittorrent.

        `requester` is who the torrent is for (mentioned on completion,
        owner of its controls); it differs from the person interacting
        when an administrator approves someone else's request.
        """
        requester = requester or interaction.user

        # The search picker has already deferred by the time it gets here.
        if not interaction.response.is_done():
            await interaction.response.defer()

        if not category:
            await interaction.followup.send(
                "Please select a category when using **Add**."
            )
            return

        if category not in self.ALLOWED_CATEGORIES:
            await interaction.followup.send(
                "That qBittorrent category is not available "
                "through Aegis-Bot."
            )
            return

        # Preserve compatibility with the existing direct /torrent add
        # command while allowing search results to supply a generic URL.
        if source_url is None:
            source_url = magnet

        if not source_url or not source_url.strip():
            await interaction.followup.send(
                "Please provide a torrent source when using **Add**."
            )
            return

        source_url = source_url.strip()

        # With REQUEST_APPROVAL on, others' torrents wait for an
        # administrator instead of being added now.
        if (
            not approved
            and approval_required()
            and not is_admin(interaction.user)
        ):
            await ask_approval(self, interaction, category, source_url)
            return

        service = QBittorrentService()

        try:
            logger.info(
                "[Torrent] Adding torrent source for category '%s' "
                "requested by %s.",
                category,
                requester,
            )

            torrent_hash = await service.add_torrent(
                magnet_url=source_url,
                category=category,
            )

            if not torrent_hash:
                await interaction.followup.send(
                    "qBittorrent accepted the request, but did not "
                    "return a torrent hash. The torrent may still "
                    "have been added."
                )
                return

            logger.info(
                "[Torrent] qBittorrent returned torrent hash %s.",
                torrent_hash,
            )

            # Notifications about this torrent go to whoever added it.
            remember(
                torrent_hash,
                requester.id,
            )

            torrent = None

            try:
                torrents = await asyncio.wait_for(
                    service.get_torrents(
                        category=category,
                    ),
                    timeout=3,
                )

                for candidate in torrents:
                    candidate_hash = str(
                        candidate.get(
                            "hash",
                            "",
                        )
                    ).lower()

                    if candidate_hash == torrent_hash.lower():
                        torrent = candidate
                        break

            except asyncio.TimeoutError:
                logger.warning(
                    "[Torrent] Timed out while retrieving details "
                    "for newly added torrent %s.",
                    torrent_hash,
                )

            except Exception as ex:
                logger.warning(
                    "[Torrent] Failed to retrieve details for "
                    "newly added torrent %s: %s",
                    torrent_hash,
                    ex,
                )

        except Exception as ex:
            logger.exception(
                "[Torrent] Failed to add torrent: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to add the torrent to qBittorrent."
            )
            return

        finally:
            await service.close()

        if torrent is None:
            torrent = {
                "hash": torrent_hash,
                "name": "Resolving torrent metadata…",
                "category": category,
                "state": "",
                "progress": 0,
            }

        view = TorrentControlView(
            owner_id=requester.id,
            torrent_hash=torrent_hash,
            torrent_name=torrent.get(
                "name",
                "Resolving torrent metadata…",
            ),
            torrent=torrent,
            torrent_cog=self,
        )

        if torrent.get(
            "name"
        ) == "Resolving torrent metadata…":
            description = (
                "The torrent was added to qBittorrent.\n"
                "Waiting for torrent metadata…"
            )
        else:
            description = (
                "The torrent was added and is being monitored "
                "automatically."
            )

        embed = self._build_info_embed(
            interaction,
            torrent,
        )

        # After an approval the footer names who asked, not who approved.
        if requester is not interaction.user:
            embed.set_footer(
                text=(
                    f"Requested by {requester.display_name} · "
                    f"approved by {interaction.user.display_name}"
                ),
                icon_url=requester.display_avatar.url,
            )

        embed.title = "Torrent Added"
        embed.description = description

        await interaction.edit_original_response(
            content=None,
            embed=embed,
            view=view,
        )

        view.message = await interaction.original_response()
        view.start_refresh()

        # A magnet often has no size until qBittorrent has fetched its
        # metadata, so the space check runs in the background.
        asyncio.create_task(
            self._warn_if_no_space(
                interaction,
                torrent_hash,
                category,
            )
        )

        logger.info(
            "[Torrent] Successfully added torrent '%s' "
            "(hash=%s, category=%s) for %s.",
            torrent.get(
                "name",
                "Unknown torrent",
            ),
            torrent_hash,
            torrent.get(
                "category",
                category,
            ),
            requester,
        )

    # How long to wait for a magnet's size, and how often to look.
    SPACE_CHECK_TRIES = 12
    SPACE_CHECK_INTERVAL = 10

    async def _warn_if_no_space(
        self,
        interaction: discord.Interaction,
        torrent_hash: str,
        category: str,
    ) -> None:
        """
        Tell the requester when a new torrent may not fit on the disk.

        Compares what is left to download with the free space
        qBittorrent reports for its default save path. Only warns;
        the torrent is left as it is.
        """
        service = QBittorrentService()

        try:
            for attempt in range(self.SPACE_CHECK_TRIES):
                if attempt:
                    await asyncio.sleep(self.SPACE_CHECK_INTERVAL)

                torrents = await service.get_torrents(category=category)

                torrent = next(
                    (
                        candidate
                        for candidate in torrents
                        if str(candidate.get("hash", "")).lower()
                        == torrent_hash.lower()
                    ),
                    None,
                )

                if torrent is None:
                    return

                size = int(torrent.get("size") or 0)

                if size <= 0:
                    continue

                left = int(torrent.get("amount_left") or size)
                free = await service.get_free_space()

                if free is None or left <= free:
                    return

                logger.warning(
                    "[Torrent] '%s' needs %d bytes; %d are free.",
                    torrent.get("name"),
                    left,
                    free,
                )

                await interaction.followup.send(
                    f"**Not enough free space.** "
                    f"**{torrent.get('name')}** still needs "
                    f"{format_bytes(left)}, but only "
                    f"{format_bytes(free)} is free on qBittorrent's "
                    "download disk. The torrent was added anyway; "
                    "free up space or remove it.",
                    ephemeral=True,
                )
                return

        except Exception as ex:
            logger.warning(
                "[Torrent] Free space check for %s failed: %s",
                torrent_hash,
                ex,
            )

        finally:
            await service.close()

