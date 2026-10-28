"""
Finds a PC game in a folder of games, before /torrent search looks for a
torrent.

PC_GAMES_DIR is the folder as seen from inside the bot's container. Its
folders and files (one level deep) are the games you already have;
names are cleaned with pc_title(), so "Cyberpunk.2077.v2.1-GOG" and
"Castlevania Belmonts Curse [DODI Repack]" can be found by their title.
"""

import asyncio
import os
import re
from pathlib import Path

from modules.torrents.release_name import pc_title
from utils.logger import logger


# A slow or hung network share must not stall the bot.
LIST_TIMEOUT = 10

# More entries than this are ignored; a games folder is not that large.
MAX_ENTRIES = 20000

# The query must cover at least this share of the words of a game's name.
MIN_COVERAGE = 0.6


def games_dir() -> Path | None:
    """The configured folder, or None when the check is off."""
    value = os.getenv("PC_GAMES_DIR", "").strip()

    return Path(value) if value else None


def normalise(title: str) -> str:
    """
    Words of a title for comparison. Unlike movie titles, every number
    is kept: "Football Manager 2024" and "2025" are different games.
    Only the release year pc_title() puts in parentheses is dropped.
    """
    text = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", str(title or ""))
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def matches(query: str, name: str) -> bool:
    """
    Whether a folder or file name is the game asked for.

    A shortened query may match a longer name ("The Witcher 3" finds
    "The Witcher 3 Wild Hunt"), but must cover most of it, so "Hades"
    does not claim "Hades II". A miss only costs a normal torrent search.
    """
    wanted = normalise(query).split()
    found = normalise(pc_title(name)).split()

    if not wanted or not found:
        return False

    if wanted == found:
        return True

    if not set(wanted).issubset(found):
        return False

    return len(set(wanted)) / len(set(found)) >= MIN_COVERAGE


def _list_names(folder: Path) -> list[str]:
    """Names of the entries directly in the folder (blocking)."""
    names = []

    with os.scandir(folder) as entries:
        for entry in entries:
            names.append(entry.name)

            if len(names) >= MAX_ENTRIES:
                break

    return names


async def find(query: str) -> list[str]:
    """
    Entry names in PC_GAMES_DIR that match the query, sorted.

    Nothing is looked at when the setting is empty. A missing, unreadable
    or slow folder gives no hits, so the torrent search still happens.
    """
    folder = games_dir()

    if folder is None:
        return []

    try:
        names = await asyncio.wait_for(
            asyncio.to_thread(_list_names, folder),
            timeout=LIST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("[PCLibrary] The games folder did not answer in time.")
        return []
    except OSError as ex:
        logger.warning("[PCLibrary] The games folder cannot be read: %s", type(ex).__name__)
        return []

    return sorted(
        (name for name in names if matches(query, name)),
        key=str.casefold,
    )
