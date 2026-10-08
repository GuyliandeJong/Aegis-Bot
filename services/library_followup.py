"""
After a Movies or Series torrent completes: refresh the primary
Jellyfin library, wait until the title shows up, and tell the person
who asked for it.
"""

import asyncio
import os
import time

from services.jellyfin_compare import episode_numbers
from services.jellyfin_service import JellyfinService
from services.release_name import parse
from services.torrent_requesters import mention_for
from utils.logger import logger


# How often and how long to look for the title after the refresh.
CHECK_INTERVAL = 90
CHECKS = 10

# A second refresh, in case the files were still being moved into
# place when the first one ran.
REFRESH_AGAIN_AFTER = 3

# One library refresh is enough for several torrents that finish at
# about the same time.
REFRESH_SPACING = 60

_last_refresh = 0.0
_tasks: set[asyncio.Task] = set()


def enabled() -> bool:
    value = os.getenv("JELLYFIN_REFRESH_ON_COMPLETE", "true")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def start(notification_service, torrent: dict) -> None:
    """Begin the follow-up in the background; never blocks the monitor."""
    category = str(torrent.get("category") or "")

    if not enabled() or category not in {"Movies", "Series"}:
        return

    task = asyncio.create_task(follow_up(notification_service, torrent))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _refresh(service: JellyfinService, server_name: str, force=False) -> None:
    global _last_refresh

    if not force and time.monotonic() - _last_refresh < REFRESH_SPACING:
        return

    _last_refresh = time.monotonic()
    await service.refresh(server_name)


async def in_library(service: JellyfinService, category: str, release: dict) -> dict | None:
    """
    Return the primary library's match when the release is there.

    A movie must be found by title (and year, when the name has one).
    For a series, the series being there is not enough: the episode, or
    for a season pack any episode of that season, must be too.
    """
    query = release["title"]

    if category == "Movies" and release["year"]:
        query = f"{query} {release['year']}"

    matches = await service.find_matches(query, category)
    primary = next((m for m in matches if m.get("primary")), None)

    if primary is None or category == "Movies":
        return primary

    if release["season"] is None or not primary.get("item_id"):
        return None

    episodes = await service.get_series_episodes(
        primary["server"],
        primary["item_id"],
    )

    for episode in episodes:
        if episode.get("ParentIndexNumber") != release["season"]:
            continue

        if release["episode"] is None or release["episode"] in episode_numbers(episode):
            return primary

    return None


def describe(release: dict) -> str:
    text = release["title"]

    if release["year"]:
        text += f" ({release['year']})"

    if release["season"] is not None:
        text += f" — Season {release['season']}"

        if release["episode"] is not None:
            text += f", episode {release['episode']}"

    return text


async def follow_up(notification_service, torrent: dict) -> None:
    category = str(torrent.get("category"))
    release = parse(torrent.get("name"))

    if not release["title"]:
        return

    service = JellyfinService()
    primary = service.primary_server()

    if primary is None:
        await service.close()
        return

    try:
        await _refresh(service, primary.name)

        for attempt in range(CHECKS):
            await asyncio.sleep(CHECK_INTERVAL)

            if attempt == REFRESH_AGAIN_AFTER:
                await _refresh(service, primary.name, force=True)

            match = await in_library(service, category, release)

            if match is not None:
                logger.info(
                    "[LibraryFollowup] '%s' is in %s.",
                    describe(release),
                    primary.name,
                )

                await notification_service.success(
                    title="Now in Jellyfin",
                    description=(
                        f"**{describe(release)}** is ready to watch on "
                        f"**{primary.name}**."
                    ),
                    content=mention_for(torrent.get("hash")),
                )
                return

        logger.info(
            "[LibraryFollowup] '%s' did not show up in %s within %d "
            "minutes; the download may be saved outside the library.",
            describe(release),
            primary.name,
            CHECKS * CHECK_INTERVAL // 60,
        )

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception(
            "[LibraryFollowup] Follow-up for '%s' failed.",
            torrent.get("name"),
        )

    finally:
        await service.close()
