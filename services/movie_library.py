"""
The movies of all Jellyfin servers as one list, with watched state.

Movies are matched between servers by their TMDb or IMDb id, and by
title and year when a server has no ids for one. Watched state comes
from each movie's own UserData.Played for the user in
JELLYFIN_<n>_USER; it is None for a server without that setting.
"""

import asyncio
import re
import time

from services.jellyfin_service import JellyfinService
from utils.logger import logger


def _normalize(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _keys(movie: dict) -> list[str]:
    """Every key this movie can be matched by, strongest first."""
    ids = movie.get("ProviderIds") or {}
    keys = []

    for provider in ("Tmdb", "Imdb"):
        value = str(ids.get(provider) or "").strip()

        if value:
            keys.append(f"{provider.lower()}:{value}")

    keys.append(f"title:{_normalize(str(movie.get('Name') or ''))}|{movie.get('ProductionYear') or ''}")
    return keys


def combine(per_server: list[tuple[str, list[dict], bool]]) -> list[dict]:
    """
    Merge the movie lists of several servers.

    `per_server` holds (server name, movies, watched state known).
    Returns movies sorted by title, each:
        {"title", "year", "servers": {server name: True / False / None}}
    where True is watched, False not watched and None unknown.
    """
    movies: list[dict] = []
    index: dict[str, dict] = {}

    for server, items, known in per_server:
        for item in items:
            title = str(item.get("Name") or "").strip()

            if not title:
                continue

            played = bool((item.get("UserData") or {}).get("Played")) if known else None
            keys = _keys(item)
            movie = next((index[key] for key in keys if key in index), None)

            if movie is None:
                movie = {"title": title, "year": item.get("ProductionYear"), "servers": {}}
                movies.append(movie)

            # A movie listed twice on one server (two versions) counts
            # as watched when either copy is.
            previous = movie["servers"].get(server)
            movie["servers"][server] = played if previous is None else (previous or bool(played))

            for key in keys:
                index.setdefault(key, movie)

    movies.sort(key=lambda m: (_normalize(m["title"]).removeprefix("the "), m["year"] or 0))
    return movies


def watched_anywhere(movie: dict) -> bool:
    return any(state is True for state in movie["servers"].values())


def find(movies: list[dict], search: str) -> list[dict]:
    """
    Movies whose title contains every word of `search`.

    An exact title comes first, then titles starting with the search,
    then the rest, each group in title order.
    """
    query = _normalize(search)
    words = query.split()

    if not words:
        return []

    def rank(movie: dict) -> int:
        title = _normalize(movie["title"])

        if title == query:
            return 0

        if title.startswith(query):
            return 1

        return 2

    found = [
        movie for movie in movies
        if all(word in _normalize(movie["title"]) for word in words)
    ]
    found.sort(key=rank)
    return found


# The merged list is kept for a while for autocomplete, which Discord
# calls on every keystroke and gives three seconds to answer.
CACHE_SECONDS = 600
_cache: dict = {"at": 0.0, "movies": []}
_refreshing: set[asyncio.Task] = set()


def _remember(movies: list[dict]) -> None:
    _cache["at"] = time.monotonic()
    _cache["movies"] = movies


async def _refresh() -> None:
    service = JellyfinService()

    try:
        movies, answered, _ = await load(service)

        if answered:
            _remember(movies)
    except Exception as ex:
        logger.warning("[Movies] Refreshing the movie list failed: %s", ex)
    finally:
        await service.close()


def cached() -> list[dict]:
    """
    The last merged list, at once; starts a refresh in the background
    when it is older than CACHE_SECONDS. Empty until the first load.
    """
    if time.monotonic() - _cache["at"] >= CACHE_SECONDS and not _refreshing:
        task = asyncio.create_task(_refresh())
        _refreshing.add(task)
        task.add_done_callback(_refreshing.discard)

    return _cache["movies"]


async def load(service: JellyfinService) -> tuple[list[dict], list[str], list[str]]:
    """
    Read every server at once.

    Returns (combined movies, servers that answered, servers that did
    not). A server answering with no movies at all is counted as not
    answering: a library is never really empty.
    """
    results = await asyncio.gather(
        *(service.list_movies_watched(server.name) for server in service.servers)
    )
    per_server, failed = [], []

    for server, (items, known) in zip(service.servers, results):
        if items:
            per_server.append((server.name, items, known))
        else:
            failed.append(server.name)

    movies = combine(per_server)

    if per_server:
        _remember(movies)

    return movies, [name for name, _, _ in per_server], failed
