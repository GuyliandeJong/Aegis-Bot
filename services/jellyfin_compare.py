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


def missing_keys(entries: list[dict]) -> list[str]:
    """Stable keys for missing content, to remember what was reported."""
    keys = []

    for entry in entries:
        if entry["complete"]:
            keys.append(f"S{entry['number']:02d}")
            continue

        for episode in entry["episodes"]:
            for number in episode_numbers(episode):
                keys.append(f"S{entry['number']:02d}E{number:02d}")

    return sorted(set(keys))


def _normalize(title) -> str:
    title = str(title or "").lower()
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _provider_keys(item: dict) -> set[str]:
    ids = item.get("ProviderIds")

    if not isinstance(ids, dict):
        return set()

    return {
        f"{name.lower()}:{str(value).strip().lower()}"
        for name, value in ids.items()
        if name.lower() in {"tvdb", "tmdb", "imdb"}
        and str(value or "").strip()
    }


def match_series(own: list[dict], theirs: list[dict]) -> list[tuple[dict, dict]]:
    """
    Pair series that are the same show in two libraries.

    A shared TVDB, TMDB or IMDb id decides. Without one, the
    normalized title and the year must both be equal; a title alone
    is not enough, because remakes share names.
    """
    by_provider: dict[str, dict] = {}
    by_title: dict[tuple[str, int], dict] = {}

    for item in theirs:
        for key in _provider_keys(item):
            by_provider.setdefault(key, item)

        year = item.get("ProductionYear")

        if isinstance(year, int):
            by_title.setdefault((_normalize(item.get("Name")), year), item)

    pairs = []
    used = set()

    for item in own:
        match = None

        for key in _provider_keys(item):
            if key in by_provider:
                match = by_provider[key]
                break

        if match is None:
            year = item.get("ProductionYear")

            if isinstance(year, int):
                match = by_title.get((_normalize(item.get("Name")), year))

        if match is not None and id(match) not in used:
            used.add(id(match))
            pairs.append((item, match))

    return pairs


def quality_score(quality: dict | None, preferences: dict) -> int | None:
    """
    Score a copy the way torrent results are scored: resolution plus
    codec, from the category's torrent_preferences.json section.
    Returns None when the quality is unknown.
    """
    if not quality or not quality.get("resolution"):
        return None

    resolutions = preferences.get("resolution_scores") or {}
    codecs = preferences.get("codec_scores") or {}

    score = int(resolutions.get(quality["resolution"], 0) or 0)

    codec_scores = [
        int(codecs[key])
        for key in quality.get("codec_keys") or []
        if key in codecs
    ]

    if codec_scores:
        score += max(codec_scores)

    return score


def describe_quality(quality: dict | None, format_size) -> str | None:
    """"1080p · HEVC · 8.20 GiB", leaving out what is unknown."""
    if not quality:
        return None

    parts = [
        part
        for part in (
            quality.get("resolution"),
            quality.get("codec"),
            format_size(quality["size"]) if quality.get("size") else None,
        )
        if part
    ]

    return " · ".join(parts) or None


def better_copy(matches: list[dict], preferences: dict) -> dict | None:
    """
    Return the other server's match that fits the preferences better
    than the primary library's copy, or None.
    """
    primary = next((m for m in matches if m.get("primary")), None)

    if primary is None:
        return None

    own = quality_score(primary.get("quality"), preferences)

    if own is None:
        return None

    best = None
    best_score = own

    for match in matches:
        if match is primary:
            continue

        score = quality_score(match.get("quality"), preferences)

        if score is not None and score > best_score:
            best = match
            best_score = score

    return best
