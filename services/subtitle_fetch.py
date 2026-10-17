"""
Download missing subtitles for movies through the primary Jellyfin
server's subtitle providers (the OpenSubtitles plugin).

Jellyfin does the work: the bot asks it to search its providers for
one movie and language, picks the best result and asks Jellyfin to
download it. Jellyfin saves the file next to the movie. Because that
call answers "done" even when the provider refused (a daily limit),
the item is read again afterwards to confirm the subtitle is there.

Used in two places:
    - right after a downloaded movie shows up in Jellyfin
      (services/library_followup.py)
    - once a day for movies already in the library, a few at a time
      (monitors/subtitle_monitor.py)

.env:
    SUBTITLE_LANGUAGE          language to fetch (default eng)
    SUBTITLE_AUTO_DOWNLOAD     true (default) or false
    SUBTITLE_DAILY_LIMIT       movies tried per daily run (default 10,
                               0 turns the daily run off)
    SUBTITLE_HEARING_IMPAIRED  prefer hearing-impaired subtitles
                               (default false)
"""

import asyncio
import json
import os
import time
from pathlib import Path

from services import activity_log
from services.jellyfin_service import JellyfinService
from utils.logger import logger


STATE_FILE = Path("/app/data/subtitle_fetch.json")

# After a download request, how long to wait for the file to appear.
CONFIRM_CHECKS = 3
CONFIRM_DELAY = 10

# A movie for which nothing was found is tried again after this long.
RETRY_DAYS = 30


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def enabled() -> bool:
    return _flag("SUBTITLE_AUTO_DOWNLOAD", "true")


def daily_limit() -> int:
    try:
        return max(0, int(os.getenv("SUBTITLE_DAILY_LIMIT", "10")))
    except ValueError:
        return 10


def hearing_impaired() -> bool:
    return _flag("SUBTITLE_HEARING_IMPAIRED", "false")


# ----------------------------------------------------------------
# Choosing a result
# ----------------------------------------------------------------


def pick(results: list[dict], prefer_hearing_impaired: bool = False) -> dict | None:
    """
    The best search result, or None.

    Forced (partial) and machine or AI translated subtitles are never
    taken. Then, in order: an exact match for this file (hash), the
    wanted hearing-impaired setting, most downloads, highest rating.
    """
    usable = [
        result
        for result in results
        if result.get("Id")
        and not result.get("Forced")
        and not result.get("MachineTranslated")
        and not result.get("AiTranslated")
    ]

    if not usable:
        return None

    def number(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    return max(
        usable,
        key=lambda r: (
            bool(r.get("IsHashMatch")),
            bool(r.get("HearingImpaired")) == prefer_hearing_impaired,
            number(r.get("DownloadCount")),
            number(r.get("CommunityRating")),
        ),
    )


# ----------------------------------------------------------------
# One movie
# ----------------------------------------------------------------


async def fetch(service: JellyfinService, server_name: str, item_id: str) -> dict:
    """
    Make sure one movie has a subtitle in SUBTITLE_LANGUAGE.

    Returns {"result": ..., "provider": ..., "name": ...} where result
    is one of:
        present  the movie already had one
        added    one was downloaded and Jellyfin shows it
        none     the providers had nothing usable
        refused  Jellyfin accepted the download but no file appeared,
                 usually the provider's daily download limit
        error    the search or the download request failed
    """
    item = await service.get_item(server_name, item_id)

    if item is None:
        return {"result": "error"}

    if service.subtitle_info(item)["preferred"]:
        return {"result": "present"}

    language = service.preferred_subtitle_language()
    results = await service.search_remote_subtitles(server_name, item_id, language)

    if results is None:
        return {"result": "error"}

    choice = pick(results, hearing_impaired())

    if choice is None:
        return {"result": "none"}

    outcome = {
        "provider": str(choice.get("ProviderName") or "the subtitle provider"),
        "name": str(choice.get("Name") or ""),
    }

    if not await service.download_remote_subtitle(server_name, item_id, str(choice["Id"])):
        return {"result": "error", **outcome}

    for _ in range(CONFIRM_CHECKS):
        await asyncio.sleep(CONFIRM_DELAY)
        item = await service.get_item(server_name, item_id)

        if item is not None and service.subtitle_info(item)["preferred"]:
            return {"result": "added", **outcome}

    return {"result": "refused", **outcome}


def describe(outcome: dict) -> str | None:
    """One line for Discord, or None when there is nothing to say."""
    language = JellyfinService.language_name(JellyfinService.preferred_subtitle_language())
    provider = outcome.get("provider") or "the subtitle provider"

    return {
        "added": f"Subtitles: {language} added from {provider}.",
        "none": f"Subtitles: no {language} subtitles found.",
        "refused": (
            f"Subtitles: {language} found on {provider}, but no file was saved. "
            "The daily download limit may be reached; it is tried again tomorrow."
        ),
        "error": (
            "Subtitles: the search failed. Check that the OpenSubtitles "
            "plugin is installed and logged in on Jellyfin."
        ),
    }.get(outcome.get("result"))


# ----------------------------------------------------------------
# Remembering what was tried
# ----------------------------------------------------------------


def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as ex:
        logger.error("[Subtitles] Failed to load %s: %s", STATE_FILE, ex)
        return {}


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=4))
        temporary.replace(STATE_FILE)
    except Exception as ex:
        logger.error("[Subtitles] Failed to save %s: %s", STATE_FILE, ex)


def remember(item_id: str, title: str, outcome: dict) -> None:
    """Record the outcome, for the daily run and the weekly summary."""
    result = outcome.get("result")

    if result in ("added", "none", "refused"):
        activity_log.record("subtitles", result=result, title=title)

    # Only "nothing found" is worth waiting a month for; a refused or
    # failed attempt is tried again on the next run.
    if result == "none":
        state = _load()
        state[item_id] = {"result": result, "at": time.time(), "title": title}
        _save(state)


def _tried_recently(state: dict, item_id: str, now: float) -> bool:
    entry = state.get(item_id)

    if not isinstance(entry, dict):
        return False

    return now - float(entry.get("at") or 0) < RETRY_DAYS * 86400


def _title(item: dict) -> str:
    name = str(item.get("Name") or "Unknown")
    year = item.get("ProductionYear")
    return f"{name} ({year})" if year else name


# ----------------------------------------------------------------
# The daily run over the existing library
# ----------------------------------------------------------------


async def backfill(service: JellyfinService, limit: int) -> dict:
    """
    Try up to `limit` movies of the primary library that lack a
    subtitle in SUBTITLE_LANGUAGE, newest additions first.

    Stops early when a download is refused: the provider's daily limit
    is then reached and further tries would only waste requests.
    Returns the number of movies per result.
    """
    counts = {"added": 0, "none": 0, "refused": 0, "error": 0, "missing": 0}
    primary = service.primary_server()

    if primary is None or limit <= 0:
        return counts

    movies = await service.list_movies(primary.name)
    state = _load()
    now = time.time()

    candidates = []

    for movie in movies:
        item_id = str(movie.get("Id") or "")
        info = service.subtitle_info(movie)

        # Without stream information Jellyfin has not scanned the file
        # yet; it is picked up on a later day.
        if not item_id or not info["known"] or info["preferred"]:
            continue

        counts["missing"] += 1

        if not _tried_recently(state, item_id, now):
            candidates.append(movie)

    for movie in candidates[:limit]:
        item_id = str(movie["Id"])
        title = _title(movie)
        outcome = await fetch(service, primary.name, item_id)
        result = outcome["result"]

        if result in counts:
            counts[result] += 1

        remember(item_id, title, outcome)
        logger.info("[Subtitles] %s: %s", title, result)

        if result == "refused":
            logger.warning(
                "[Subtitles] A download was refused; stopping until the "
                "next run (provider daily limit?)."
            )
            break

    return counts
