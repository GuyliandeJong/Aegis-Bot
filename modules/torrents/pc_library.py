"""
Finds a PC game in a folder of games, before /torrent search looks for a
torrent.

PC_GAMES_DIR is the folder as seen from inside the bot's container. Its
folders, and installers and disc images (.iso, .zip, .rar, .7z, .exe,
.msi, .img), one level deep, are the games you already have; names are
cleaned with pc_title(), so "Cyberpunk.2077.v2.1-GOG" and
"Castlevania Belmonts Curse [DODI Repack]" can be found by their title.
"""

import asyncio
import os
import re
from collections import Counter
from pathlib import Path

from modules.torrents.release_name import pc_title
from utils.logger import logger


# A slow or hung network share must not stall the bot.
LIST_TIMEOUT = 10

# More entries than this are ignored; a games folder is not that large.
MAX_ENTRIES = 20000

# Files that count as a game (folders always do): installers and disc
# images. Notes, pictures and checksums next to the games do not.
GAME_FILES = {".iso", ".zip", ".rar", ".7z", ".exe", ".msi", ".img"}

# Words of a search that need not be in the name.
STOP_WORDS = {"a", "an", "and", "of", "the"}


def games_dir() -> Path | None:
    """The configured folder, or None when the check is off."""
    value = os.getenv("PC_GAMES_DIR", "").strip()

    return Path(value) if value else None


# "Game of the Year Edition", "Deluxe Edition", "GOTY": the same game,
# so they do not count as words of its name.
EDITION = re.compile(
    r"\b(?:(?:game of the year|complete|deluxe|definitive|ultimate|enhanced|"
    r"gold|premium|collectors|special|anniversary|legendary|standard|"
    r"digital|royal)\s+)?edition\b|\bgoty\b"
)


def normalise(title: str) -> str:
    """
    Words of a title for comparison. Unlike movie titles, every number
    is kept: "Football Manager 2024" and "2025" are different games.
    Only the release year pc_title() puts in parentheses is dropped.
    """
    text = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", str(title or ""))
    text = text.lower().replace("'", "").replace("’", "")
    text = EDITION.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def matches(query: str, name: str) -> bool:
    """
    Whether a folder or file name is the game asked for: every word of
    the search is in the name, as often as it was typed ("Chop Chop"
    finds "Chop Chop Inc (2026)", "Chop" does too).

    A short search can match several games ("Hades" lists "Hades II"
    as well). That is deliberate: the answer shows the names and offers
    to search anyway, while a missed match could download a game you
    already have. Numbers count, so 2024 is not 2025.
    """
    words = normalise(query).split()
    wanted = Counter(w for w in words if w not in STOP_WORDS) or Counter(words)
    found = Counter(normalise(pc_title(name)).split())

    if not wanted or not found:
        return False

    return all(found[word] >= count for word, count in wanted.items())


def _list_names(folder: Path) -> list[str]:
    """Names of the games directly in the folder (blocking)."""
    names = []

    with os.scandir(folder) as entries:
        for entry in entries:
            try:
                game = (
                    entry.is_dir()
                    or Path(entry.name).suffix.lower() in GAME_FILES
                )
            except OSError:
                continue

            if not game:
                continue

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

    exact = normalise(query)

    # The name that is exactly the search first, then the rest.
    return sorted(
        (name for name in names if matches(query, name)),
        key=lambda name: (normalise(pc_title(name)) != exact, name.casefold()),
    )
