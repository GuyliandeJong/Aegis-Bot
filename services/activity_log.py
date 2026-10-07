"""
A short history of what the bot did, for the weekly summary.

Records alerts and Jellyfin copies with a timestamp. Torrent
completions are not recorded here: qBittorrent keeps their completion
time itself.
"""

import json
import time
from pathlib import Path

from utils.logger import logger


STATE_FILE = Path("/app/data/activity_log.json")

# Entries older than this are dropped when the log is saved.
KEEP_SECONDS = 60 * 24 * 60 * 60
MAX_ENTRIES = 5000

_entries: list[dict] | None = None


def _load() -> list[dict]:
    global _entries

    if _entries is not None:
        return _entries

    _entries = []

    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())

            if isinstance(data, list):
                _entries = [e for e in data if isinstance(e, dict)]

        except Exception as ex:
            logger.error("[ActivityLog] Failed to load %s: %s", STATE_FILE, ex)

    return _entries


def record(kind: str, **fields) -> None:
    """Add an entry, e.g. record("alert", level="warning", title=...)."""
    entries = _load()
    entries.append({"time": int(time.time()), "kind": kind, **fields})

    cutoff = time.time() - KEEP_SECONDS
    entries[:] = [e for e in entries if e.get("time", 0) >= cutoff][-MAX_ENTRIES:]

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries))
        temporary.replace(STATE_FILE)
    except Exception as ex:
        logger.error("[ActivityLog] Failed to save %s: %s", STATE_FILE, ex)


def since(timestamp: float, kind: str | None = None) -> list[dict]:
    return [
        entry
        for entry in _load()
        if entry.get("time", 0) >= timestamp
        and (kind is None or entry.get("kind") == kind)
    ]
