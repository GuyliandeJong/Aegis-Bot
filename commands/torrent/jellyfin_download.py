"""Copy a movie or series season from another Jellyfin server."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import discord

from commands.torrent.formatting import TorrentFormattingMixin
from services.jellyfin_service import (
    JellyfinDownloadError,
    JellyfinService,
)
from utils.formatting import format_bytes
from utils.logger import logger


@dataclass
class TransferFile:
    """One media file to copy."""

    item_id: str
    filename: str
    directory: Path


# Media files are large, so only one transfer runs at a time.
_active_transfer: "JellyfinTransfer | None" = None


def _library_folder(match: dict) -> str:
    """Return the "Title (Year)" folder name for a Jellyfin match."""
    title = str(match.get("title") or "Unknown title")
    year = match.get("year")

    if year:
        title = f"{title} ({year})"

    return JellyfinService.safe_name(title)


def _status_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
    )


# A Discord request that has not finished after this long is treated
# as failed, so a stalled request shows an error instead of leaving
# the message untouched.
DISCORD_TIMEOUT = 30


async def _send(step: str, request):
    """
    Await one Discord request, logging when it starts and finishes.

    The log lines make it possible to tell which step a stuck
    download stopped at.
    """
    logger.info("[JellyfinDownload] %s: sending.", step)

    try:
        result = await asyncio.wait_for(
            request,
            timeout=DISCORD_TIMEOUT,
        )
    except asyncio.TimeoutError as ex:
        raise RuntimeError(
            f"Discord did not answer within {DISCORD_TIMEOUT} seconds "
            f"({step})."
        ) from ex

    logger.info("[JellyfinDownload] %s: done.", step)

    return result


async def _report_error(
    interaction: discord.Interaction,
    error: Exception,
) -> None:
    """
    Log an unexpected failure and show it in Discord.

    Without this the message would stay on its last "Looking up…"
    text forever, with nothing to explain why.
    """
    logger.error(
        "[JellyfinDownload] Unexpected error for %s: %s",
        interaction.user,
        error,
        exc_info=error,
    )

    embed = discord.Embed(
        title="Jellyfin download failed",
        description=(
            "Something went wrong while preparing the download.\n\n"
            f"`{type(error).__name__}: {str(error)[:300]}`"
        ),
        color=discord.Color.red(),
    )

    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=None,
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

    except discord.HTTPException as ex:
        logger.warning(
            "[JellyfinDownload] Could not show the error in Discord: %s",
            ex,
        )


class JellyfinTransfer:
    """
    Runs one download in the background and keeps a Discord message
    up to date with its progress.
    """

    UPDATE_INTERVAL = 10

    def __init__(
        self,
        interaction: discord.Interaction,
        server_name: str,
        title: str,
        files: list[TransferFile],
    ):
        self.interaction = interaction
        self.server_name = server_name
        self.title = title
        self.files = files
        self.user = interaction.user

        self.message = None
        self.task: asyncio.Task | None = None

        self.index = 0
        self.received = 0
        self.total = 0
        self.started_at = time.monotonic()

        self.downloaded: list[str] = []
        self.skipped: list[str] = []
        self.error: str | None = None
        self.refreshed = False

    def _on_progress(self, received: int, total: int) -> None:
        self.received = received
        self.total = total

    def progress_embed(self) -> discord.Embed:
        """Build the embed shown while the transfer is running."""
        current = self.files[
            min(self.index, len(self.files) - 1)
        ]

        lines = [
            f"**{self.title}**",
            f"Copying from **{self.server_name}** into your library.",
            "",
            f"File {self.index + 1} of {len(self.files)}",
            f"`{current.filename or 'Waiting for file name…'}`",
        ]

        if self.total:
            lines.append(
                TorrentFormattingMixin._format_progress_bar(
                    self.received / self.total
                )
            )
            lines.append(
                f"{format_bytes(self.received)} of "
                f"{format_bytes(self.total)}"
            )

        elif self.received:
            lines.append(
                f"{format_bytes(self.received)} received"
            )

        return _status_embed(
            "Downloading from Jellyfin…",
            "\n".join(lines),
        )

    def result_embed(self) -> discord.Embed:
        """Build the embed shown when the transfer has ended."""
        lines = [f"**{self.title}**", ""]

        if self.downloaded:
            lines.append(
                f"Copied {len(self.downloaded)} file(s) "
                f"from **{self.server_name}**."
            )

        if self.skipped:
            lines.append(
                f"Skipped {len(self.skipped)} file(s) that were "
                "already in your library."
            )

        if self.error:
            remaining = (
                len(self.files)
                - len(self.downloaded)
                - len(self.skipped)
            )

            lines.append("")
            lines.append(f"**Stopped:** {self.error}")

            if remaining > 1:
                lines.append(
                    f"{remaining} file(s) were not copied."
                )

        elif self.downloaded:
            lines.append(
                "Your Jellyfin library is being refreshed."
                if self.refreshed
                else "Your Jellyfin library could not be refreshed "
                "automatically; start a library scan to see the "
                "new files."
            )

        if self.error:
            title = "Jellyfin download failed"
            color = discord.Color.red()
        elif self.downloaded:
            title = "Jellyfin download complete"
            color = discord.Color.green()
        else:
            title = "Nothing to download"
            color = discord.Color.blurple()

        return discord.Embed(
            title=title,
            description="\n".join(lines),
            color=color,
        )

    async def start(self) -> None:
        """
        Begin the transfer.

        The interaction must already have been answered with the
        message that will show the progress.
        """
        global _active_transfer

        _active_transfer = self

        self.message = await _send(
            "progress message lookup",
            self._durable_message(),
        )

        self.task = asyncio.create_task(
            self._run()
        )

    async def _durable_message(self):
        """
        Return a handle to the progress message that keeps working.

        A message obtained from an interaction can only be edited for
        fifteen minutes. Large downloads take longer, so fetch the
        same message through its channel instead.
        """
        message = await self.interaction.original_response()

        try:
            return await self.interaction.channel.fetch_message(
                message.id
            )
        except Exception:
            return message

    async def _edit(self, embed: discord.Embed) -> None:
        if self.message is None:
            return

        try:
            await self.message.edit(
                content=None,
                embed=embed,
                view=None,
            )
        except discord.HTTPException as ex:
            logger.warning(
                "[JellyfinDownload] Could not update the progress "
                "message: %s",
                ex,
            )

    async def _update_loop(self) -> None:
        while True:
            await asyncio.sleep(self.UPDATE_INTERVAL)
            await self._edit(self.progress_embed())

    async def _run(self) -> None:
        global _active_transfer

        service = JellyfinService()

        updater = asyncio.create_task(
            self._update_loop()
        )

        logger.info(
            "[JellyfinDownload] %s started copying '%s' "
            "(%d file(s)) from %s.",
            self.user,
            self.title,
            len(self.files),
            self.server_name,
        )

        try:
            for index, file in enumerate(self.files):
                self.index = index
                self.received = 0
                self.total = 0

                try:
                    path, downloaded = await service.download_item(
                        self.server_name,
                        file.item_id,
                        file.directory,
                        file.filename,
                        on_progress=self._on_progress,
                    )

                except JellyfinDownloadError as ex:
                    self.error = str(ex)
                    break

                if downloaded:
                    self.downloaded.append(path.name)
                else:
                    self.skipped.append(path.name)

            primary = service.primary_server()

            if self.downloaded and primary is not None:
                self.refreshed = await service.refresh(
                    primary.name
                )

        except Exception:
            logger.exception(
                "[JellyfinDownload] Unexpected error while copying '%s'.",
                self.title,
            )

            self.error = (
                "An unexpected error occurred. Check the bot logs."
            )

        finally:
            updater.cancel()
            await service.close()
            _active_transfer = None

        logger.info(
            "[JellyfinDownload] Finished '%s': %d copied, %d skipped, "
            "error=%s.",
            self.title,
            len(self.downloaded),
            len(self.skipped),
            self.error,
        )

        await self._edit(self.result_embed())
        await self._notify()

    async def _notify(self) -> None:
        """Mention the requester, because a download can take a while."""
        if self.message is None or not (self.downloaded or self.error):
            return

        outcome = "failed" if self.error else "finished"

        try:
            await self.message.channel.send(
                f"{self.user.mention} the Jellyfin download of "
                f"**{self.title}** has {outcome}.",
                allowed_mentions=discord.AllowedMentions(
                    users=True
                ),
            )
        except Exception as ex:
            logger.warning(
                "[JellyfinDownload] Could not send the completion "
                "message: %s",
                ex,
            )


async def _reject_if_busy(interaction: discord.Interaction) -> bool:
    """Tell the user when another transfer is still running."""
    if _active_transfer is None:
        return False

    await interaction.response.send_message(
        f"A Jellyfin download of **{_active_transfer.title}** is "
        "already running. Please wait for it to finish.",
        ephemeral=True,
    )

    return True


class JellyfinSeasonView(discord.ui.View):
    """Owner-restricted season picker for a series download."""

    def __init__(
        self,
        owner_id: int,
        match: dict,
        seasons: list[dict],
        directory: Path,
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.match = match
        self.directory = directory

        # Discord allows 25 options, each needing a unique value.
        self.seasons = {}

        for season in seasons:
            if not isinstance(season, dict) or not season.get("Id"):
                continue

            self.seasons[str(season["Id"])] = season

            if len(self.seasons) == 25:
                break

        self.select = discord.ui.Select(
            placeholder="Choose a season to download…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(
                        str(season.get("Name") or "").strip()
                        or f"Season {season.get('IndexNumber')}"
                    )[:100],
                    value=season_id[:100],
                )
                for season_id, season in self.seasons.items()
            ],
        )

        self.select.callback = self._choose
        self.add_item(self.select)

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who started this search can choose a season.",
            ephemeral=True,
        )
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item,
    ):
        await _report_error(interaction, error)

    @staticmethod
    def _season_folder(season: dict) -> str:
        """Return the "Season 01" folder name for a season."""
        number = season.get("IndexNumber")

        if isinstance(number, int):
            return f"Season {number:02d}"

        return (
            JellyfinService.safe_name(season.get("Name"))
            or "Season Unknown"
        )

    async def _choose(self, interaction: discord.Interaction):
        if await _reject_if_busy(interaction):
            return

        self.stop()

        season = self.seasons[self.select.values[0]]
        season_name = str(season.get("Name") or "the season")
        server_name = self.match["server"]

        logger.info(
            "[JellyfinDownload] %s chose %s of '%s'.",
            interaction.user,
            season_name,
            self.match.get("title"),
        )

        await _send(
            "episode lookup notice",
            interaction.response.edit_message(
                content=None,
                embed=_status_embed(
                    "Downloading from Jellyfin…",
                    f"Looking up the episodes of **{season_name}**.",
                ),
                view=None,
            ),
        )

        service = JellyfinService()

        try:
            episodes = await service.get_episodes(
                server_name,
                self.match["item_id"],
                str(season.get("Id")),
            )
        finally:
            await service.close()

        directory = self.directory / self._season_folder(season)

        logger.info(
            "[JellyfinDownload] %s returned %d episode(s) for '%s' %s.",
            server_name,
            len(episodes),
            self.match.get("title"),
            season_name,
        )

        files = [
            TransferFile(
                item_id=str(episode.get("Id")),
                filename=JellyfinService.media_filename(episode),
                directory=directory,
            )
            for episode in episodes
            if episode.get("Id")
        ]

        if not files:
            await interaction.edit_original_response(
                embed=_status_embed(
                    "Nothing to download",
                    f"**{server_name}** did not return any episodes "
                    f"for {season_name}.",
                ),
            )
            return

        await JellyfinTransfer(
            interaction,
            server_name,
            f"{self.match.get('title')} — {season_name}",
            files,
        ).start()


class JellyfinSourceView(discord.ui.View):
    """
    Owner-restricted view shown when a title is missing from the
    primary library but another Jellyfin server has it.
    """

    def __init__(
        self,
        owner_id: int,
        query: str,
        category: str,
        torrent_cog,
        match: dict,
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.query = query
        self.category = category
        self.torrent_cog = torrent_cog
        self.match = match

        self.download_button = discord.ui.Button(
            label=f"Download from {match['server']}"[:80],
            style=discord.ButtonStyle.success,
        )

        self.download_button.callback = self._download
        self.add_item(self.download_button)

        # Imported here because the search module imports this one.
        from commands.torrent.search import TorrentJellyfinSearchButton

        self.add_item(
            TorrentJellyfinSearchButton(self)
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who started this search can use this button.",
            ephemeral=True,
        )
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item,
    ):
        await _report_error(interaction, error)

    async def _download(self, interaction: discord.Interaction):
        if await _reject_if_busy(interaction):
            return

        library = JellyfinService.library_dir(self.category)

        if library is None:
            await interaction.response.send_message(
                "No library folder is configured for this category.",
                ephemeral=True,
            )
            return

        self.stop()

        directory = library / _library_folder(self.match)
        server_name = self.match["server"]

        logger.info(
            "[JellyfinDownload] %s pressed download for '%s' (%s) on %s.",
            interaction.user,
            self.match.get("title"),
            self.category,
            server_name,
        )

        if self.category == "Series":
            await self._choose_season(
                interaction,
                server_name,
                directory,
            )
            return

        transfer = JellyfinTransfer(
            interaction,
            server_name,
            _library_folder(self.match),
            [
                TransferFile(
                    item_id=self.match["item_id"],
                    filename=JellyfinService.media_filename(self.match),
                    directory=directory,
                )
            ],
        )

        await interaction.response.edit_message(
            content=None,
            embed=transfer.progress_embed(),
            view=None,
        )

        await transfer.start()

    async def _choose_season(
        self,
        interaction: discord.Interaction,
        server_name: str,
        directory: Path,
    ):
        await _send(
            "season lookup notice",
            interaction.response.edit_message(
                content=None,
                embed=_status_embed(
                    "Download from Jellyfin",
                    f"Looking up the seasons of "
                    f"**{self.match.get('title')}**.",
                ),
                view=None,
            ),
        )

        service = JellyfinService()

        try:
            seasons = await service.get_seasons(
                server_name,
                self.match["item_id"],
            )
        finally:
            await service.close()

        view = JellyfinSeasonView(
            owner_id=self.owner_id,
            match=self.match,
            seasons=seasons,
            directory=directory,
        )

        logger.info(
            "[JellyfinDownload] %s returned %d season(s) for '%s'; "
            "%d can be offered.",
            server_name,
            len(seasons),
            self.match.get("title"),
            len(view.seasons),
        )

        if not view.seasons:
            await interaction.edit_original_response(
                embed=_status_embed(
                    "Nothing to download",
                    f"**{server_name}** did not return any seasons "
                    "for this series.",
                ),
            )
            return

        await _send(
            "season picker",
            interaction.edit_original_response(
                content=None,
                embed=_status_embed(
                    "Download from Jellyfin",
                    f"**{self.match.get('title')}** on **{server_name}**\n\n"
                    "Choose the season to copy into your library. "
                    "Only that season is downloaded.",
                ),
                view=view,
            ),
        )
