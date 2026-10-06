"""Torrent control actions."""
import discord
from services.qbittorrent_service import QBittorrentService
from utils import theme
from utils.embeds import create_embed
from utils.logger import logger
from commands.torrent.views import TorrentControlView
class TorrentControlMixin:
    """Mixin providing torrent control operations."""
    async def _change_torrent_state(
        self,
        interaction: discord.Interaction,
        action: str,
        category: str | None,
        query: str | None,
    ):
        """
        Change the state of a torrent.
        This method is retained for compatibility with the previous
        command architecture. New user-facing controls are provided
        through TorrentControlView.
        """
        await interaction.response.defer()
        if not query or not query.strip():
            await interaction.followup.send(
                "Please provide a torrent name or part of a torrent name."
            )
            return
        if category is not None and category not in self.ALLOWED_CATEGORIES:
            await interaction.followup.send(
                "That qBittorrent category is not available through Aegis-Bot."
            )
            return
        search_query = query.strip().lower()
        service = QBittorrentService()
        try:
            torrents = await service.get_torrents(
                category=category,
            )
        except Exception as ex:
            logger.exception(
                "[Torrent] Failed to retrieve torrents for %s: %s",
                action,
                ex,
            )
            await interaction.followup.send(
                "Unable to retrieve the torrent list from qBittorrent."
            )
            return
        finally:
            await service.close()
        torrents = [
            torrent
            for torrent in torrents
            if torrent.get("category") in self.ALLOWED_CATEGORIES
        ]
        matches = [
            torrent
            for torrent in torrents
            if search_query
            in str(
                torrent.get(
                    "name",
                    "",
                )
            ).lower()
        ]
        if not matches:
            await interaction.followup.send(
                f"No torrent matching `{query}` was found."
            )
            return
        if len(matches) > 1:
            await interaction.followup.send(
                f"Multiple torrents matched `{query}`. "
                "Please use a more specific query."
            )
            return
        torrent = matches[0]
        torrent_hash = torrent.get("hash")
        if not torrent_hash:
            await interaction.followup.send(
                "The matching torrent did not contain a valid qBittorrent hash."
            )
            return
        service = QBittorrentService()
        try:
            if action == "pause":
                await service.pause_torrent(
                    torrent_hash
                )
                message = (
                    f"Torrent **{torrent.get('name', 'Unknown')}** "
                    "was stopped."
                )
            elif action == "resume":
                await service.resume_torrent(
                    torrent_hash
                )
                message = (
                    f"Torrent **{torrent.get('name', 'Unknown')}** "
                    "was started."
                )
            elif action == "delete":
                await service.delete_torrent(
                    torrent_hash,
                    delete_files=False,
                )
                message = (
                    f"Torrent **{torrent.get('name', 'Unknown')}** "
                    "was removed from qBittorrent.\n\n"
                    "Downloaded files were preserved."
                )
            else:
                await interaction.followup.send(
                    f"Unknown torrent action: `{action}`."
                )
                return
        except Exception as ex:
            logger.exception(
                "[Torrent] Failed to %s torrent '%s': %s",
                action,
                torrent.get(
                    "name",
                    "Unknown",
                ),
                ex,
            )
            await interaction.followup.send(
                f"Unable to {action} the torrent through qBittorrent."
            )
            return
        finally:
            await service.close()
        embed = create_embed(
            interaction=interaction,
            title=(
                f"{theme.STORAGE_EMOJI} "
                "qBittorrent — Torrent Control"
            ),
            description=message,
        )
        await interaction.followup.send(
            embed=embed
        )
        logger.info(
            "[Torrent] Torrent action '%s' performed on '%s' by %s.",
            action,
            torrent.get(
                "name",
                "Unknown",
            ),
            interaction.user,
        )