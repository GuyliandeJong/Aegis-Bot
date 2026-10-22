"""
What was watched on the primary Jellyfin server, for the weekly summary.

Jellyfin keeps no viewing history of its own; the Playback Reporting
plugin (jellyfin/jellyfin-plugin-playbackreporting) records every play
in an SQLite table and accepts SQL through
POST /user_usage_stats/submit_custom_query. The table, as the plugin
creates it (Data/ActivityRepository.cs):

    PlaybackActivity (DateCreated DATETIME, UserId TEXT, ItemId TEXT,
                      ItemType TEXT, ItemName TEXT, PlaybackMethod TEXT,
                      ClientName TEXT, DeviceName TEXT, PlayDuration INT)

DateCreated is the Jellyfin server's local time as text
("2026-10-18 21:04:11.1234567"), PlayDuration is in seconds, an
episode's ItemName is "Series - s01e02 - Episode title", and
PlaybackMethod is "DirectPlay", "DirectStream", "Transcode (v:h264
a:aac)" or "na" (EventMonitorEntryPoint.cs).
"""

import os
import re
from collections import Counter
from datetime import datetime

from modules.jellyfin.jellyfin_service import JellyfinService
from utils.logger import logger


# Music is listened to, not watched.
SKIPPED_TYPES = ("Audio", "AudioBook")

TOP = 5

# "Fallout - s02e03 - The Other Player" -> "Fallout"
EPISODE_NAME = re.compile(r"^(.*?) - s\d+e\d+ - ", re.IGNORECASE)

# A user removed from Jellyfin keeps their id instead of a name.
USER_ID = re.compile(r"^[0-9a-f]{32}$")


def enabled() -> bool:
    value = os.getenv("WEEKLY_SUMMARY_WATCH_STATS", "true")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def query(since: float) -> str:
    """
    Every play since the given time, leaving out music and the users
    excluded in the plugin's own settings (its UserList table), as the
    plugin's reports do.
    """
    # Only digits, dashes and colons, so it is safe inside the SQL.
    start = datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S")
    skipped = ", ".join(f"'{kind}'" for kind in SKIPPED_TYPES)

    return (
        "SELECT UserId, ItemType, ItemName, PlaybackMethod, PlayDuration "
        "FROM PlaybackActivity "
        f"WHERE DateCreated >= '{start}' "
        f"AND ItemType NOT IN ({skipped}) "
        "AND UserId NOT IN (SELECT UserId FROM UserList)"
    )


def hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f} h"


def title_of(item_type: str, name: str) -> str:
    """Episodes count towards their series; anything else by its name."""
    if item_type == "Episode":
        match = EPISODE_NAME.match(name)

        if match:
            return match.group(1).strip() or name

    return name or "Unknown"


def summarize(columns: list, rows: list) -> str:
    """The field text for the plays the plugin returned."""
    index = {name: position for position, name in enumerate(columns)}

    def cell(row, name):
        position = index.get(name)

        if position is None or position >= len(row):
            return ""

        return str(row[position] or "")

    users: Counter = Counter()
    titles: Counter = Counter()
    total = 0
    known_method = 0
    transcoded = 0

    for row in rows:
        try:
            seconds = max(0, int(float(cell(row, "PlayDuration") or 0)))
        except ValueError:
            seconds = 0

        user = cell(row, "UserName") or cell(row, "UserId") or "?"

        if USER_ID.match(user):
            user = "Removed user"

        total += seconds
        users[user] += seconds
        titles[title_of(cell(row, "ItemType"), cell(row, "ItemName"))] += seconds

        method = cell(row, "PlaybackMethod")

        if method and method != "na":
            known_method += 1

            if method.startswith("Transcode"):
                transcoded += 1

    plays = len(rows)
    lines = [f"{hours(total)} in {plays} play{'s' if plays != 1 else ''}."]

    lines.append(
        "Per user: " + ", ".join(
            f"{user} {hours(seconds)}" for user, seconds in users.most_common(TOP)
        )
    )

    lines.append("Top titles:")
    lines += [
        f"• {title[:60]} · {hours(seconds)}"
        for title, seconds in titles.most_common(TOP)
    ]

    # Older plugin versions, or a client that did not say, give "na".
    if known_method:
        lines.append(
            f"Transcoded: {transcoded} of {known_method} plays "
            f"({transcoded / known_method * 100:.0f}%)."
        )

    return "\n".join(lines)


async def field(since: float) -> str | None:
    """
    The "Watched this week" field, or None when the field is turned off
    or no Jellyfin server is configured.
    """
    if not enabled():
        return None

    service = JellyfinService()
    server = service.primary_server()

    if server is None:
        return None

    try:
        status, data = await service.playback_query(server.name, query(since))
    finally:
        await service.close()

    if status == 404:
        return f"Needs the Playback Reporting plugin on {server.name}."

    if status == 0:
        return f"{server.name} did not answer."

    if status != 200 or not isinstance(data, dict):
        return f"{server.name} refused the query (HTTP {status})."

    columns = data.get("colums") or data.get("columns") or []
    rows = data.get("results") or []

    # The plugin lists columns from the first row, so no rows means
    # no columns and a "no data returned" message, which is fine.
    if not rows:
        return "Nothing watched."

    if not isinstance(columns, list) or "PlayDuration" not in columns:
        logger.warning(
            "[WatchStats] Unexpected answer from %s: %s",
            server.name,
            str(data.get("message") or data)[:300],
        )
        return f"Unexpected answer from {server.name}."

    return summarize(columns, [row for row in rows if isinstance(row, list)])
