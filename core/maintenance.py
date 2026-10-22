"""
Maintenance mode: alerts other than critical are muted for a while.

The state lives in data/maintenance.json, so a restart keeps
maintenance active. NotificationService consults it before every
alert and posts the start and end messages; `/maintenance`
(modules/general/commands/maintenance.py) and MaintenanceMonitor only call that
service. This module does not talk to Discord itself.
"""

import json
import time
from collections import Counter
from pathlib import Path

from utils.logger import logger


STATE_FILE = Path("/app/data/maintenance.json")

# Levels muted during maintenance. Critical alerts always go through.
MUTED_LEVELS = {"warning", "recovered", "info", "success"}

# At most this many muted titles are kept for the end message; the
# counts per level keep going.
MAX_MUTED = 200

_state: dict | None = None


def _load() -> dict:
    global _state

    if _state is not None:
        return _state

    _state = {}

    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())

            if isinstance(data, dict) and data.get("until"):
                _state = data

        except Exception as ex:
            logger.error("[Maintenance] Failed to load %s: %s", STATE_FILE, ex)

    return _state


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(_load(), indent=4))
        temporary.replace(STATE_FILE)
    except Exception as ex:
        logger.error("[Maintenance] Failed to save %s: %s", STATE_FILE, ex)


def current() -> dict | None:
    """The maintenance in progress, also when it has just expired."""
    state = _load()
    return dict(state) if state.get("until") else None


def active() -> bool:
    state = _load()
    return bool(state.get("until")) and time.time() < state["until"]


def expired() -> bool:
    """True when maintenance ran out but its end was not reported yet."""
    state = _load()
    return bool(state.get("until")) and time.time() >= state["until"]


def start(minutes: int, reason: str, started_by: str) -> tuple[dict, bool]:
    """
    Start maintenance, or move the end of the current one.

    Returns the state and whether maintenance was already active.
    Alerts muted so far are kept when it is extended.
    """
    state = _load()
    extended = active()

    if not extended:
        state.clear()
        state.update({"started": int(time.time()), "muted": [], "counts": {}})

    state.update({
        "until": int(time.time() + minutes * 60),
        "reason": reason,
        "started_by": started_by,
    })
    _save()

    return dict(state), extended


def mute(level: str, title: str) -> None:
    """Remember an alert that was not posted."""
    state = _load()
    counts = state.setdefault("counts", {})
    counts[level] = counts.get(level, 0) + 1

    muted = state.setdefault("muted", [])

    if len(muted) < MAX_MUTED:
        muted.append({"level": level, "title": title})

    _save()


def end() -> dict | None:
    """
    Stop maintenance and return its state, or None when none was on.

    Clearing happens before anything is awaited by the caller, so of
    two callers noticing the expiry at once only one gets the state.
    """
    state = _load()

    if not state.get("until"):
        return None

    ended = dict(state)
    state.clear()
    _save()

    return ended


def muted_summary(state: dict, limit: int = 3800) -> tuple[int, str]:
    """
    The number of muted alerts and their titles grouped and counted,
    most frequent first, e.g. "• Datastore Filling Up ×3".
    """
    total = sum(state.get("counts", {}).values())
    titles = Counter(
        entry.get("title", "?")
        for entry in state.get("muted", [])
    )

    lines = []
    shown = 0

    for title, count in titles.most_common():
        line = f"• {title}" + (f" ×{count}" if count > 1 else "")

        if sum(len(x) + 1 for x in lines) + len(line) > limit:
            break

        lines.append(line)
        shown += count

    # Titles beyond MAX_MUTED, or that did not fit, are only counted.
    if total > shown:
        lines.append(f"…and {total - shown} more")

    return total, "\n".join(lines)
