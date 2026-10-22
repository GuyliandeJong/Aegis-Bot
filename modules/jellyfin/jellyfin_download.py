"""Copy a movie or series season from another Jellyfin server."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import discord

from modules.torrents.torrent.formatting import TorrentFormattingMixin
from core import activity_log
from modules.jellyfin.jellyfin_compare import missing_content
from modules.jellyfin.jellyfin_service import (
    JellyfinDownloadError,
    JellyfinService,
)
from utils.formatting import format_bytes
from utils.logger import logger
from utils import open_messages
from utils.open_messages import ExpiringView


@dataclass
class TransferFile:
    """One media file to copy."""

    item_id: str
    filename: str
    directory: Path

    # Whether the file has subtitles in the preferred language.
    # None when the other server did not report its streams.
    has_subtitles: bool | None = None

    # Separate subtitle files to save next to the video.
    media_source_id: str = ""
    subtitle_streams: list[int] = field(default_factory=list)


def _transfer_file(
    item_id: str,
    filename: str,
    directory: Path,
    subtitles: dict | None,
    media_source_id: str = "",
) -> TransferFile:
    """Build a TransferFile, including its subtitle information."""
    known = bool(subtitles and subtitles.get("known"))

    return TransferFile(
        item_id=item_id,
        filename=filename,
        directory=directory,
        has_subtitles=(
            subtitles["preferred"] is not None
            if known
            else None
        ),
        media_source_id=media_source_id,
        subtitle_streams=(
            list(subtitles["external"])
            if known
            else []
        ),
    )


def _subtitle_line(files: list[TransferFile]) -> str | None:
    """Summarize preferred-language subtitles across a transfer."""
    known = [
        file
        for file in files
        if file.has_subtitles is not None
    ]

    if not known:
        return None

    name = JellyfinService.language_name(
        JellyfinService.preferred_subtitle_language()
    )

    count = sum(1 for file in known if file.has_subtitles)

    if count == 0:
        return f"{name} subtitles: none"

    if len(known) == 1:
        return f"{name} subtitles: yes"

    if count == len(known):
        return f"{name} subtitles: in all {count} files"

    return f"{name} subtitles: in {count} of {len(known)} files"


def existing_series_folder(primary_match: dict, library: Path) -> str | None:
    """
    Return the folder a series already uses in the primary library.

    Missing seasons must land next to the seasons that are already
    there. The primary server reports the folder with its own path, so
    only the folder name is used, and only if it exists in the library
    as this container sees it.
    """
    name = re.split(
        r"[\\/]",
        str(primary_match.get("path") or "").rstrip("\\/"),
    )[-1].strip()

    if not name or name in {".", ".."}:
        return None

    if (library / name).is_dir():
        return name

    return None


def season_folder_name(
    number: int,
    own_episodes: list[dict],
    series_dir: Path | None,
) -> str:
    """
    Return the folder a season's episodes belong in.

    When the library already has episodes of that season, their folder
    is reused, whatever it is called ("Season 3", "Season 03", "S03").
    Otherwise the folder is "Season NN".
    """
    if series_dir is not None:
        for episode in own_episodes:
            if episode.get("ParentIndexNumber") != number:
                continue

            parts = re.split(
                r"[\\/]",
                str(episode.get("Path") or ""),
            )

            if len(parts) < 2:
                continue

            name = parts[-2].strip()

            if name and name not in {".", ".."} and (
                series_dir / name
            ).is_dir():
                return name

    return f"Season {number:02d}"


async def find_missing_content(matches: list[dict], category: str):
    """
    Find episodes another server has that the primary library lacks.

    Whole seasons and single episodes are both found: each server is
    asked once for every episode of the series, and the episode
    numbers are compared.

    Returns a dict with source, primary, missing (see
    jellyfin_compare.missing_content) and own_episodes, or None when
    there is nothing to offer or the comparison cannot be made.
    """
    if category != "Series":
        return None

    if JellyfinService.library_dir(category) is None:
        return None

    primary = next(
        (
            match
            for match in matches
            if match.get("primary") and match.get("item_id")
        ),
        None,
    )

    if primary is None:
        return None

    service = JellyfinService()

    try:
        own = await service.get_series_episodes(
            primary["server"],
            primary["item_id"],
        )

        # Without the primary library's own episodes there is nothing
        # to compare against, and everything would look missing.
        if not own:
            return None

        for match in matches:
            if match.get("primary") or not match.get("item_id"):
                continue

            theirs = await service.get_series_episodes(
                match["server"],
                match["item_id"],
            )

            missing = missing_content(own, theirs)

            if missing:
                return {
                    "source": match,
                    "primary": primary,
                    "missing": missing,
                    "own_episodes": own,
                }

    finally:
        await service.close()

    return None


# Media files are large, so transfers run one at a time. Requests made
# while one runs wait here, in order.
_queue: list["JellyfinTransfer"] = []
_active_transfer: "JellyfinTransfer | None" = None
_worker: asyncio.Task | None = None


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


async def expire_view(view: discord.ui.View) -> None:
    """
    Show that a message's buttons no longer work.

    A view stops listening after its timeout, but Discord keeps
    showing its buttons as if they still worked. Disable them and say
    what to do next.
    """
    for item in view.children:
        item.disabled = True

    message = getattr(view, "message", None)

    if message is None:
        logger.warning(
            "[JellyfinDownload] %s expired, but its message is "
            "unknown; the buttons could not be disabled.",
            type(view).__name__,
        )
        return

    try:
        await message.edit(
            content=(
                "This request has expired. "
                "Run `/torrent search` again to continue."
            ),
            view=view,
        )

        logger.info(
            "[JellyfinDownload] %s expired; buttons disabled.",
            type(view).__name__,
        )

    except Exception as ex:
        logger.warning(
            "[JellyfinDownload] %s expired, but the message could "
            "not be updated: %s",
            type(view).__name__,
            ex,
        )


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

        self.index = 0
        self.received = 0
        self.total = 0
        self.started_at = time.monotonic()

        self.downloaded: list[str] = []
        self.skipped: list[str] = []
        self.error: str | None = None
        self.refreshed = False
        self.subtitle_files = 0
        self.copied_bytes = 0

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

        subtitle_line = _subtitle_line(self.files)

        if subtitle_line:
            lines.append("")
            lines.append(subtitle_line)

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

        subtitle_line = _subtitle_line(self.files)

        if subtitle_line and (self.downloaded or self.skipped):
            lines.append(subtitle_line)

            if self.subtitle_files:
                lines.append(
                    f"Saved {self.subtitle_files} separate "
                    "subtitle file(s)."
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
        Queue the transfer; it begins when the ones before it are done.

        The interaction must already have been answered with the
        message that will show the progress.
        """
        global _worker

        self.message = await _send(
            "progress message lookup",
            self._durable_message(),
        )

        open_messages.remember(
            self.message,
            note=(
                "The bot restarted during this download, so it "
                "stopped. Request it again: files that were already "
                "copied are skipped."
            ),
            owner="transfer",
        )

        _queue.append(self)

        if _active_transfer is not None or len(_queue) > 1:
            logger.info(
                "[JellyfinDownload] '%s' queued at position %d.",
                self.title,
                len(_queue),
            )
            await self._edit(self.queued_embed())

        if _worker is None or _worker.done():
            _worker = asyncio.create_task(_run_queue())

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

    def queued_embed(self) -> discord.Embed:
        """Build the embed shown while the transfer waits its turn."""
        position = _queue.index(self) + 1 if self in _queue else 1
        lines = [
            f"**{self.title}**",
            f"Waiting to copy from **{self.server_name}**.",
            "",
            f"Position {position} in the queue.",
        ]

        if _active_transfer is not None:
            lines.append(
                f"Now copying: **{_active_transfer.title}**"
            )

        subtitle_line = _subtitle_line(self.files)

        if subtitle_line:
            lines.append("")
            lines.append(subtitle_line)

        return _status_embed(
            "Jellyfin download queued",
            "\n".join(lines),
        )

    async def _run(self) -> None:
        service = JellyfinService()
        self.started_at = time.monotonic()

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

                    try:
                        self.copied_bytes += path.stat().st_size
                    except OSError:
                        pass
                else:
                    self.skipped.append(path.name)

                # Subtitles inside the video file come along by
                # themselves; separate subtitle files are fetched here.
                for number, stream_index in enumerate(
                    file.subtitle_streams
                ):
                    name = JellyfinService.subtitle_filename(path.name)

                    if number:
                        name = name.replace(
                            ".srt",
                            f".{number + 1}.srt",
                        )

                    if await service.download_subtitle(
                        self.server_name,
                        file.item_id,
                        file.media_source_id,
                        stream_index,
                        path.with_name(name),
                    ):
                        self.subtitle_files += 1

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

        logger.info(
            "[JellyfinDownload] Finished '%s': %d copied, %d skipped, "
            "error=%s.",
            self.title,
            len(self.downloaded),
            len(self.skipped),
            self.error,
        )

        if self.downloaded:
            activity_log.record(
                "jellyfin_copy",
                title=self.title,
                server=self.server_name,
                files=len(self.downloaded),
                bytes=self.copied_bytes,
            )

        await self._edit(self.result_embed())
        open_messages.forget(self.message, "transfer")
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


async def _run_queue() -> None:
    """Run queued transfers one after another until the queue is empty."""
    global _active_transfer

    while _queue:
        transfer = _queue.pop(0)
        _active_transfer = transfer

        # The others moved up one place.
        for waiting in list(_queue):
            await waiting._edit(waiting.queued_embed())

        try:
            await transfer._edit(transfer.progress_embed())
            await transfer._run()
        except Exception:
            logger.exception(
                "[JellyfinDownload] Transfer of '%s' failed.",
                transfer.title,
            )
        finally:
            _active_transfer = None


def is_idle() -> bool:
    """True when no transfer is running or waiting."""
    return _active_transfer is None and not _queue


def _season_choice_label(season: dict) -> str:
    return (
        str(season.get("Name") or "").strip()
        or f"Season {season.get('IndexNumber')}"
    )


def _default_season_folder(season: dict) -> str:
    """Return the "Season 01" folder name for a season."""
    number = season.get("IndexNumber")

    if isinstance(number, int):
        return f"Season {number:02d}"

    return (
        JellyfinService.safe_name(season.get("Name"))
        or "Season Unknown"
    )


class JellyfinSeasonView(ExpiringView):
    """
    Owner-restricted season picker for a series download.

    Each choice is a dict with:
        season:    the season ({"Id", "Name", "IndexNumber"})
        episodes:  the episodes to copy, or None to copy the whole
                   season (looked up when chosen)
        directory: the folder the files go into
        label:     the text shown in the picker
    """

    def __init__(
        self,
        owner_id: int,
        match: dict,
        choices: list[dict],
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.match = match
        self.message = None

        # Discord allows 25 options.
        self.choices = [
            choice
            for choice in choices
            if isinstance(choice.get("season"), dict)
        ][:25]

        self.select = discord.ui.Select(
            placeholder="Choose a season to download…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=choice["label"][:100],
                    value=str(index),
                )
                for index, choice in enumerate(self.choices)
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

    async def on_timeout(self):
        await expire_view(self)

    async def _choose(self, interaction: discord.Interaction):
        self.stop()

        choice = self.choices[int(self.select.values[0])]
        season = choice["season"]
        season_name = _season_choice_label(season)
        server_name = self.match["server"]
        episodes = choice.get("episodes")

        logger.info(
            "[JellyfinDownload] %s chose %s of '%s'.",
            interaction.user,
            choice["label"],
            self.match.get("title"),
        )

        if episodes is None:
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

            logger.info(
                "[JellyfinDownload] %s returned %d episode(s) for "
                "'%s' %s.",
                server_name,
                len(episodes),
                self.match.get("title"),
                season_name,
            )
            title = f"{self.match.get('title')} — {season_name}"

        else:
            await _send(
                "missing episodes notice",
                interaction.response.edit_message(
                    content=None,
                    embed=_status_embed(
                        "Downloading from Jellyfin…",
                        f"Preparing **{choice['label']}**.",
                    ),
                    view=None,
                ),
            )
            count = len(episodes)
            title = (
                f"{self.match.get('title')} — {season_name}"
                if choice.get("complete")
                else f"{self.match.get('title')} — {season_name} "
                f"({count} episode{'s' if count != 1 else ''})"
            )

        files = [
            _transfer_file(
                str(episode.get("Id")),
                JellyfinService.media_filename(episode),
                choice["directory"],
                JellyfinService.subtitle_info(episode),
                JellyfinService.media_source_id(episode),
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
            title,
            files,
        ).start()


def missing_choices(
    missing: list[dict],
    own_episodes: list[dict],
    series_dir: Path,
) -> list[dict]:
    """Turn missing content into season picker choices."""
    choices = []

    for entry in missing:
        season = entry["season"]
        name = _season_choice_label(season)

        if entry["complete"]:
            label = name
        else:
            count = len(entry["episodes"])
            label = (
                f"{name} — {count} missing "
                f"episode{'s' if count != 1 else ''}"
            )

        choices.append(
            {
                "season": season,
                "episodes": entry["episodes"],
                "directory": series_dir / season_folder_name(
                    entry["number"],
                    own_episodes,
                    series_dir,
                ),
                "label": label,
                "complete": entry["complete"],
            }
        )

    return choices


class JellyfinSourceView(ExpiringView):
    """
    Owner-restricted view shown when another Jellyfin server has a
    title, or episodes of it, that the primary library lacks.
    """

    def __init__(
        self,
        owner_id: int,
        query: str,
        category: str,
        torrent_cog,
        match: dict,
        missing: list[dict] | None = None,
        own_episodes: list[dict] | None = None,
        folder_name: str | None = None,
        timeout: float = 180,
    ):
        super().__init__(timeout=timeout)

        self.owner_id = owner_id
        self.query = query
        self.category = category
        self.torrent_cog = torrent_cog
        self.match = match
        self.message = None

        # Set when the primary library already has part of a series:
        # only the missing seasons and episodes are offered, and they
        # are copied into the folders the series already uses.
        self.missing = missing
        self.own_episodes = own_episodes or []
        self.folder_name = folder_name

        if missing is not None:
            label = f"Download missing from {match['server']}"
        else:
            label = f"Download from {match['server']}"

        self.download_button = discord.ui.Button(
            label=label[:80],
            style=discord.ButtonStyle.success,
        )

        self.download_button.callback = self._download
        self.add_item(self.download_button)

        # Imported here because the search module imports this one.
        from modules.torrents.torrent.search import TorrentJellyfinSearchButton

        if torrent_cog is not None:
            self.add_item(
                TorrentJellyfinSearchButton(self)
            )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if self.owner_id is None or interaction.user.id == self.owner_id:
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

    async def on_timeout(self):
        await expire_view(self)

    async def _download(self, interaction: discord.Interaction):
        library = JellyfinService.library_dir(self.category)

        if library is None:
            await interaction.response.send_message(
                "No library folder is configured for this category.",
                ephemeral=True,
            )
            return

        self.stop()

        directory = library / (
            self.folder_name
            or _library_folder(self.match)
        )
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
                _transfer_file(
                    self.match["item_id"],
                    JellyfinService.media_filename(self.match),
                    directory,
                    self.match.get("subtitles"),
                    self.match.get("media_source_id") or "",
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

        if self.missing is not None:
            # The missing episodes were already looked up for the
            # availability message.
            choices = missing_choices(
                self.missing,
                self.own_episodes,
                directory,
            )
            found = len(self.missing)

        else:
            service = JellyfinService()

            try:
                seasons = await service.get_seasons(
                    server_name,
                    self.match["item_id"],
                )
            finally:
                await service.close()

            choices = [
                {
                    "season": season,
                    "episodes": None,
                    "directory": directory / _default_season_folder(season),
                    "label": _season_choice_label(season),
                }
                for season in seasons
                if isinstance(season, dict) and season.get("Id")
            ]
            found = len(seasons)

        view = JellyfinSeasonView(
            owner_id=self.owner_id,
            match=self.match,
            choices=choices,
        )

        logger.info(
            "[JellyfinDownload] %s returned %d season(s) for '%s'; "
            "%d can be offered.",
            server_name,
            found,
            self.match.get("title"),
            len(view.choices),
        )

        if not view.choices:
            await interaction.edit_original_response(
                embed=_status_embed(
                    "Nothing to download",
                    f"**{server_name}** did not return any seasons "
                    "for this series.",
                ),
            )
            return

        view.message = await _send(
            "season picker",
            interaction.edit_original_response(
                content=None,
                embed=_status_embed(
                    "Download from Jellyfin",
                    f"**{self.match.get('title')}** on **{server_name}**\n\n"
                    "Choose the season to copy into your library. "
                    "Only what you choose is downloaded.",
                ),
                view=view,
            ),
        )
