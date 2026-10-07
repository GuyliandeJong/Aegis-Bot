"""Torrent add command implementation."""

import asyncio

import discord

from commands.torrent.views import TorrentControlView
from services.qbittorrent_service import QBittorrentService
from services.torrent_requesters import remember
from utils.logger import logger


class TorrentAddMixin:
    """Mixin providing torrent adding."""

    async def _add(
        self,
        interaction: discord.Interaction,
        category: str | None,
        source_url: str | None = None,
        magnet: str | None = None,
    ):
        """Add a torrent source to qBittorrent."""
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

        service = QBittorrentService()

        try:
            logger.info(
                "[Torrent] Adding torrent source for category '%s' "
                "requested by %s.",
                category,
                interaction.user,
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
                interaction.user.id,
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
            owner_id=interaction.user.id,
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

        embed.title = "Torrent Added"
        embed.description = description

        await interaction.edit_original_response(
            content=None,
            embed=embed,
            view=view,
        )

        view.message = await interaction.original_response()
        view.start_refresh()

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
            interaction.user,
        )