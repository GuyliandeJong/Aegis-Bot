"""
The movies of all Jellyfin servers as one list, with watched state.

Movies are matched between servers by their TMDb or IMDb id, and by
title and year when a server has no ids for one. Watched state comes
from each movie's own UserData.Played for the user in
JELLYFIN_<n>_USER; it is None for a server without that setting.
"""

import asyncio
import re

from services.jellyfin_service import JellyfinService


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


FILTERS = {
    "all": "All",
    "unwatched": "Not watched",
    "watched": "Watched",
    "not_mine": "Not in my library",
}


def apply(movies: list[dict], show: str, search: str, primary: str) -> list[dict]:
    """Filter the combined list for /jellyfin movies."""
    words = _normalize(search).split()

    def wanted(movie: dict) -> bool:
        title = _normalize(movie["title"])

        if words and not all(word in title.split() or word in title for word in words):
            return False

        if show == "unwatched":
            return not watched_anywhere(movie)

        if show == "watched":
            return watched_anywhere(movie)

        if show == "not_mine":
            return primary not in movie["servers"]

        return True

    return [movie for movie in movies if wanted(movie)]


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

    return combine(per_server), [name for name, _, _ in per_server], failed
