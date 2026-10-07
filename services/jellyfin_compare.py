"""
Compare what two Jellyfin libraries hold.

Pure functions on Jellyfin API data, so they can be checked without a
server: which episodes one library lacks, which series in two
libraries are the same show, and which copy of a movie fits the
torrent preferences better.
"""

import re


def _number(value) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def episode_numbers(episode: dict) -> list[int]:
    """
    Return the episode numbers a file covers.

    A double episode ("S01E01-E02") reports IndexNumber 1 and
    IndexNumberEnd 2 and covers both.
    """
    first = _number(episode.get("IndexNumber"))

    if first is None:
        return []

    last = _number(episode.get("IndexNumberEnd"))

    if last is None or last < first or last - first > 20:
        return [first]

    return list(range(first, last + 1))


def missing_content(own: list[dict], theirs: list[dict]) -> list[dict]:
    """
    Return what `theirs` has that `own` lacks, grouped per season.

    Both lists are episodes as Jellyfin returns them. Each result is:
        number:   the season number
        season:   {"Id", "Name", "IndexNumber"} of their season
        episodes: their episodes to copy
        complete: True when own has no episode of that season at all

    Conservative on purpose: a season in which own has an episode
    without a number, or with no season number, is never offered
    partly, because that file may well be one of the "missing" ones
    under a name Jellyfin could not parse.
    """
    own_episodes: dict[int, set[int]] = {}
    unclear_seasons: set[int] = set()

    for episode in own:
        season = _number(episode.get("ParentIndexNumber"))

        if season is None:
            continue

        numbers = episode_numbers(episode)

        if not numbers:
            unclear_seasons.add(season)

        own_episodes.setdefault(season, set()).update(numbers)

    result: dict[int, dict] = {}

    for episode in theirs:
        season = _number(episode.get("ParentIndexNumber"))
        numbers = episode_numbers(episode)

        if season is None or not numbers or not episode.get("Id"):
            continue

        have = own_episodes.get(season)
        complete = have is None

        if not complete:
            if season in unclear_seasons:
                continue

            if all(number in have for number in numbers):
                continue

        entry = result.setdefault(
            season,
            {
                "number": season,
                "season": {
                    "Id": str(episode.get("SeasonId") or ""),
                    "Name": str(
                        episode.get("SeasonName")
                        or f"Season {season}"
                    ),
                    "IndexNumber": season,
                },
                "episodes": [],
                "complete": complete,
            },
        )

        entry["episodes"].append(episode)

    return [result[number] for number in sorted(result)]


def describe_missing(entries: list[dict], limit: int = 6) -> str:
    """Describe missing content briefly: "Season 4, Season 3 (2 episodes)"."""
    parts = []

    for entry in entries:
        name = entry["season"]["Name"]

        if entry["complete"]:
            parts.append(name)
        else:
            count = len(entry["episodes"])
            parts.append(
                f"{name} ({count} episode{'s' if count != 1 else ''})"
            )

    if len(parts) > limit:
        rest = len(parts) - limit
        parts = parts[:limit] + [f"{rest} more"]

    return ", ".join(parts)
