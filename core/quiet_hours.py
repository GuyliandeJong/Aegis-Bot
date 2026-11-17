"""
Quiet hours: alerts other than critical are held overnight and posted
as one summary when the quiet hours end.

QUIET_HOURS is a daily window in the container's time zone, for example
"23-7" or "22:30-07:00" (it may cross midnight). Empty turns it off.
The held alerts live in data/quiet_hours.json, so a restart keeps them.
NotificationService consults this module before every alert and posts
the summary (it has the Discord side); this module does not talk to
Discord. Critical alerts and scheduled reports (`exempt`) always go
through, and maintenance mode takes precedence while it is on.
"""

import json
import os
import re
from datetime import datetime

from config.paths import DATA_DIR
from utils.logger import logger


STATE_FILE = DATA_DIR / "quiet_hours.json"

# Same limits as maintenance mode.
MAX_HELD = 200

_PATTERN = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*$"
)

_state: dict | None = None
_warned = False


def now() -> datetime:
    return datetime.now()


def window() -> tuple[int, int] | None:
    """(start, end) in minutes after midnight, or None when off."""
    global _warned

    value = os.getenv("QUIET_HOURS", "").strip()

    if not value:
        return None

    match = _PATTERN.match(value)

    if match:
        sh, sm, eh, em = (int(g) if g else 0 for g in match.groups())

        if sh < 24 and eh < 24 and sm < 60 and em < 60:
            start, end = sh * 60 + sm, eh * 60 + em

            if start != end:
                return start, end

    if not _warned:
        _warned = True
        logger.warning("[QuietHours] QUIET_HOURS=%r is not a window like 23-7; quiet hours are off.", value)

    return None


def active(moment: datetime | None = None) -> bool:
    """Whether the quiet hours are on right now."""
    span = window()

    if span is None:
        return False

    moment = moment or now()
    minute = moment.hour * 60 + moment.minute
    start, end = span

    if start < end:
        return start <= minute < end

    return minute >= start or minute < end


def _load() -> dict:
    global _state

    if _state is not None:
        return _state

    _state = {}

    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())

            if isinstance(data, dict) and data.get("counts"):
                _state = data
        except Exception as ex:
            logger.error("[QuietHours] Failed to load %s: %s", STATE_FILE, ex)

    return _state


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(_load(), indent=4))
        temporary.replace(STATE_FILE)
    except Exception as ex:
        logger.error("[QuietHours] Failed to save %s: %s", STATE_FILE, ex)


def hold(level: str, title: str) -> None:
    """Remember an alert that was not posted."""
    state = _load()
    state.setdefault("started", int(now().timestamp()))
    counts = state.setdefault("counts", {})
    counts[level] = counts.get(level, 0) + 1
    held = state.setdefault("muted", [])

    if len(held) < MAX_HELD:
        held.append({"level": level, "title": title})

    _save()


def pending() -> bool:
    """Whether alerts are waiting for the summary."""
    return bool(_load().get("counts"))


def take() -> dict | None:
    """
    The held alerts, cleared, or None when there are none. Clearing
    happens before the caller awaits anything, so of two callers noticing
    the end at once only one gets the state.
    """
    state = _load()

    if not state.get("counts"):
        return None

    taken = dict(state)
    state.clear()
    _save()

    return taken
