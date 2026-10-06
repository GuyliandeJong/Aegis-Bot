"""qBittorrent server status command implementation."""

import discord

from services.qbittorrent_service import QBittorrentService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger


class TorrentStatusMixin:
    """Mixin providing qBittorrent server status functionality."""

    async def _status(
        self,
        interaction: discord.Interaction,
    ):
        """Display qBittorrent server/application status."""

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            version = await service.get_version()
            transfer = await service.get_transfer_info()

        except Exception as ex:
            logger.exception(
                "[Torrent] Failed to retrieve qBittorrent server status: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to retrieve the qBittorrent server status."
            )
            return

        finally:
            await service.close()

        download_speed = self._format_speed(
            transfer.get("dl_info_speed", 0)
        )

        upload_speed = self._format_speed(
            transfer.get("up_info_speed", 0)
        )

        download_data = self._format_bytes(
            transfer.get("dl_info_data", 0)
        )

        upload_data = self._format_bytes(
            transfer.get("up_info_data", 0)
        )

        connection_status = str(
            transfer.get(
                "connection_status",
                "unknown",
            )
        )

        connection_status = {
            "connected": "Connected",
            "firewalled": "Firewalled",
            "disconnected": "Disconnected",
        }.get(
            connection_status.lower(),
            connection_status.title(),
        )

        fields = [
            EmbedField(
                "Version",
                str(version),
                True,
            ),
            EmbedField(
                "Connection",
                connection_status,
                True,
            ),
            EmbedField(
                "Download Speed",
                download_speed,
                True,
            ),
            EmbedField(
                "Upload Speed",
                upload_speed,
                True,
            ),
            EmbedField(
                "Downloaded",
                download_data,
                True,
            ),
            EmbedField(
                "Uploaded",
                upload_data,
                True,
            ),
        ]

        embed = create_embed(
            interaction=interaction,
            title=(
                f"{theme.STORAGE_EMOJI} "
                "qBittorrent — Server Status"
            ),
            description=(
                "Current qBittorrent application and "
                "global transfer status."
            ),
            fields=fields,
        )

        await interaction.followup.send(
            embed=embed
        )

        logger.info(
            "[Torrent] qBittorrent server status requested by %s.",
            interaction.user,
        )