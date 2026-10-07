"""Remembers which Discord user added each torrent."""

import json
from pathlib import Path

from config.settings import settings
from utils.logger import logger


# Stored next to the other runtime state, on the persistent volume.
STATE_FILE = Path("/app/data/torrent_requesters.json")

# Old entries are dropped once the file holds more than this many.
MAX_ENTRIES = 1000

_requesters: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _requesters

    if _requesters is not None:
        return _requesters

    _requesters = {}

    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r") as file:
                data = json.load(file)

            if isinstance(data, dict):
                _requesters = {
                    str(torrent_hash): str(user_id)
                    for torrent_hash, user_id in data.items()
                }

        except Exception as ex:
            logger.error(
                "[TorrentRequesters] Failed to load %s: %s",
                STATE_FILE,
                ex,
            )

    return _requesters


def _save() -> None:
    temporary = STATE_FILE.with_suffix(".tmp")

    try:
        STATE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with temporary.open("w") as file:
            json.dump(
                _load(),
                file,
                indent=4,
            )

        temporary.replace(STATE_FILE)

    except Exception as ex:
        logger.error(
            "[TorrentRequesters] Failed to save %s: %s",
            STATE_FILE,
            ex,
        )


def remember(torrent_hash, user_id) -> None:
    """Record that a Discord user added a torrent through the bot."""
    torrent_hash = str(torrent_hash or "").strip().lower()

    if not torrent_hash or not user_id:
        return

    requesters = _load()

    # Re-adding moves the entry to the end, so it is the newest.
    requesters.pop(torrent_hash, None)
    requesters[torrent_hash] = str(user_id)

    while len(requesters) > MAX_ENTRIES:
        requesters.pop(next(iter(requesters)))

    _save()


def mention_for(torrent_hash) -> str | None:
    """
    Return the Discord mention for a torrent's notifications.

    The person who added the torrent through the bot is mentioned.
    Torrents added some other way, for example in qBittorrent itself,
    fall back to DISCORD_USER_ID. Returns None when neither is known.
    """
    torrent_hash = str(torrent_hash or "").strip().lower()

    user_id = _load().get(torrent_hash)

    if not user_id:
        user_id = str(
            getattr(
                settings,
                "DISCORD_USER_ID",
                "",
            )
            or ""
        ).strip()

    return f"<@{user_id}>" if user_id.isdigit() else None
