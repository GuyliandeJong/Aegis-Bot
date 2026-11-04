"""
Finds a PC game in a folder of games, before /torrent search looks for a
torrent.

PC_GAMES_DIR is the folder as seen from inside the bot's container. Its
folders, and installers and disc images (.iso, .zip, .rar, .7z, .exe,
.msi, .img), one level deep (PC_GAMES_DIR_DEPTH for more), are the
games you already have; names are cleaned with pc_title(), so
"Cyberpunk.2077.v2.1-GOG" and "Castlevania Belmonts Curse [DODI
Repack]" can be found by their title.
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


def depth() -> int:
    """How many folder levels are searched, 1 to 3 (PC_GAMES_DIR_DEPTH)."""
    try:
        value = int(os.getenv("PC_GAMES_DIR_DEPTH", "1"))
    except ValueError:
        return 1

    return min(3, max(1, value))


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


# Roman numerals in game titles ("Hades II", "Diablo IV", "Final
# Fantasy VII") are read as numbers, so a search for "Hades 2" finds
# them. Only these multi-letter numerals count: "X" in "Mega Man X" and
# "I" in "I Am Alive" are words, and "mix" or "dim" are not numerals.
ROMAN = {
    "ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8",
    "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14",
    "xv": "15", "xvi": "16", "xvii": "17", "xviii": "18", "xix": "19",
    "xx": "20",
}


def normalise(title: str) -> str:
    """
    Words of a title for comparison. Unlike movie titles, every number
    is kept: "Football Manager 2024" and "2025" are different games.
    Only the release year pc_title() puts in parentheses is dropped.
    Roman numerals II to XX become digits (see ROMAN).
    """
    text = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", str(title or ""))
    text = text.lower().replace("'", "").replace("’", "")
    text = EDITION.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)

    return " ".join(ROMAN.get(word, word) for word in text.split())


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


def _list_names(folder: Path, levels: int = 1) -> list[str]:
    """
    Paths of the games in the folder, relative to it (blocking).

    One level lists the entries directly in the folder; more levels also
    look inside the folders found, for games kept under a publisher or
    a letter ("Publisher/Game"). Folders that are links are not entered.
    """
    names = []
    pending = [(folder, "")]

    for level in range(1, levels + 1):
        following = []

        for parent, prefix in pending:
            try:
                scanner = os.scandir(parent)
            except OSError:
                if level == 1:
                    raise
                continue

            with scanner as entries:
                for entry in entries:
                    try:
                        folder_entry = entry.is_dir()
                        game = (
                            folder_entry
                            or Path(entry.name).suffix.lower() in GAME_FILES
                        )
                        enter = (
                            folder_entry
                            and level < levels
                            and not entry.is_symlink()
                        )
                    except OSError:
                        continue

                    if not game:
                        continue

                    path = prefix + entry.name
                    names.append(path)

                    if len(names) >= MAX_ENTRIES:
                        return names

                    if enter:
                        following.append((Path(entry.path), path + "/"))

        pending = following

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
            asyncio.to_thread(_list_names, folder, depth()),
            timeout=LIST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("[PCLibrary] The games folder did not answer in time.")
        return []
    except OSError as ex:
        logger.warning("[PCLibrary] The games folder cannot be read: %s", type(ex).__name__)
        return []

    exact = normalise(query)

    # Matched on the name of the folder or file itself, shown with the
    # folders above it. The name that is exactly the search first.
    return sorted(
        (name for name in names if matches(query, _base(name))),
        key=lambda name: (
            normalise(pc_title(_base(name))) != exact,
            name.casefold(),
        ),
    )


def _base(path: str) -> str:
    return path.rsplit("/", 1)[-1]
