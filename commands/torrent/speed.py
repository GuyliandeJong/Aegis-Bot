"""`/torrent speed`: switch qBittorrent between normal and slow limits."""

import discord

from commands.torrent.approval import is_admin
from services.qbittorrent_service import QBittorrentService
from utils.formatting import format_bytes
from utils.logger import logger


def _limit(value) -> str:
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        value = 0

    return f"{format_bytes(value)}/s" if value > 0 else "unlimited"


class TorrentSpeedMixin:
    """Mixin providing `/torrent speed`."""

    async def _speed(self, interaction: discord.Interaction, mode: str | None):
        if mode is not None and not is_admin(interaction.user):
            await interaction.response.send_message(
                "Only a server administrator can change the speed. "
                "Run `/torrent speed` without a choice to see it.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        service = QBittorrentService()

        try:
            if mode is None:
                slow = await service.get_alternative_speed()
            else:
                slow = await service.set_alternative_speed(mode == "slow")

                logger.info(
                    "[Torrent] %s set the speed to %s.",
                    interaction.user,
                    "slow" if slow else "normal",
                )

            preferences = await service.get_preferences()

        except Exception as ex:
            logger.exception("[Torrent] Speed change failed: %s", ex)
            await interaction.followup.send(
                "Unable to read or change the speed in qBittorrent."
            )
            return

        finally:
            await service.close()

        if slow:
            down = _limit(preferences.get("alt_dl_limit"))
            up = _limit(preferences.get("alt_up_limit"))
        else:
            down = _limit(preferences.get("dl_limit"))
            up = _limit(preferences.get("up_limit"))

        title = "🐢 Slow speed" if slow else "🚀 Normal speed"

        if mode is not None:
            title += " is on"

        await interaction.followup.send(
            embed=discord.Embed(
                title=title,
                description=(
                    f"Download: **{down}**\n"
                    f"Upload: **{up}**\n\n"
                    "The slow limits are qBittorrent's alternative speed "
                    "limits (Options > Speed), where they can also be "
                    "switched on a schedule."
                ),
                color=(
                    discord.Color.orange()
                    if slow
                    else discord.Color.green()
                ),
            )
        )
