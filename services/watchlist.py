"""Titles people want, that were not found yet."""

import json
import time
import uuid
from pathlib import Path

from utils.logger import logger


STATE_FILE = Path("/app/data/watchlist.json")

# Per person, so one person cannot fill the list for everyone.
MAX_PER_USER = 25

_entries: list[dict] | None = None


def _load() -> list[dict]:
    global _entries

    if _entries is not None:
        return _entries

    _entries = []

    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r") as file:
                data = json.load(file)

            if isinstance(data, list):
                _entries = [
                    entry
                    for entry in data
                    if isinstance(entry, dict)
                    and entry.get("id")
                    and entry.get("query")
                ]

        except Exception as ex:
            logger.error(
                "[Watchlist] Failed to load %s: %s",
                STATE_FILE,
                ex,
            )

    return _entries


def _save() -> None:
    temporary = STATE_FILE.with_suffix(".tmp")

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        with temporary.open("w") as file:
            json.dump(_load(), file, indent=4)

        temporary.replace(STATE_FILE)

    except Exception as ex:
        logger.error(
            "[Watchlist] Failed to save %s: %s",
            STATE_FILE,
            ex,
        )


def add(query: str, category: str, user_id: int, channel_id: int) -> tuple[bool, str]:
    """
    Add a title to someone's watchlist.

    Returns (added, reason). The same title in the same category is
    only listed once per person.
    """
    query = " ".join(str(query or "").split())

    if not query:
        return False, "There is nothing to watch for."

    mine = for_user(user_id)

    for entry in mine:
        if (
            entry["query"].lower() == query.lower()
            and entry["category"] == category
        ):
            return False, f"**{query}** is already on your watchlist."

    if len(mine) >= MAX_PER_USER:
        return False, (
            f"Your watchlist is full ({MAX_PER_USER} titles). Remove "
            "one with `/torrent watchlist` first."
        )

    _load().append(
        {
            "id": uuid.uuid4().hex[:12],
            "query": query,
            "category": category,
            "user_id": str(user_id),
            "channel_id": str(channel_id or ""),
            "added": int(time.time()),
        }
    )
    _save()

    return True, ""


def remove(entry_id: str) -> dict | None:
    """Remove one entry; returns it, or None when it was not there."""
    entries = _load()

    for index, entry in enumerate(entries):
        if entry["id"] == entry_id:
            del entries[index]
            _save()
            return entry

    return None


def for_user(user_id) -> list[dict]:
    return [
        entry
        for entry in _load()
        if entry.get("user_id") == str(user_id)
    ]


def all_entries() -> list[dict]:
    return list(_load())
