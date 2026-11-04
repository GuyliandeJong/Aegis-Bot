"""qBittorrent server status command implementation."""

import discord

from modules.torrents.peers_monitor import NoPeersMonitor
from modules.torrents.vpn_monitor import VpnMonitor
from modules.torrents.qbittorrent_service import QBittorrentService
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

            # Zero DHT nodes is only a problem while DHT is turned on.
            dht = None

            if NoPeersMonitor.enabled() and transfer.get("dht_nodes") == 0:
                dht = await NoPeersMonitor.dht_enabled(service)

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

        # Only the verdict is shown, never the addresses themselves.
        if VpnMonitor.enabled():
            vpn = VpnMonitor.verdict(
                transfer,
                await VpnMonitor.home_ip(),
            )

            fields.append(
                EmbedField(
                    "VPN",
                    {
                        "ok": "OK",
                        "leak": f"{theme.WARNING_EMOJI} Not behind VPN",
                    }.get(vpn, "Unknown"),
                    True,
                )
            )

        # Nodes qBittorrent is in contact with to find peers; none
        # for a while means it needs a restart (see NoPeersMonitor).
        nodes = transfer.get("dht_nodes")

        if (
            NoPeersMonitor.enabled()
            and isinstance(nodes, int)
            and not isinstance(nodes, bool)
        ):
            if nodes > 0:
                peers = f"{nodes} DHT nodes"
            elif dht is False:
                peers = "DHT off"
            else:
                peers = f"{theme.WARNING_EMOJI} No DHT nodes"

            fields.append(EmbedField("Peers", peers, True))

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