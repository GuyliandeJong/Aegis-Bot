"""Jellyfin availability service for torrent searches."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from utils.logger import logger


@dataclass(frozen=True)
class JellyfinServer:
    name: str
    url: str
    api_key: str


class JellyfinService:
    """Check whether a requested movie or series already exists in Jellyfin."""

    def __init__(self) -> None:
        self.servers = self._load_servers()
        self.session: aiohttp.ClientSession | None = None

    @staticmethod
    def _load_servers() -> tuple[JellyfinServer, ...]:
        """Load sequentially numbered Jellyfin servers from the environment."""
        servers: list[JellyfinServer] = []
        index = 1

        while True:
            url = os.getenv(f"JELLYFIN_{index}_URL", "").strip().rstrip("/")
            api_key = os.getenv(f"JELLYFIN_{index}_API_KEY", "").strip()

            if not url and not api_key:
                break

            if not url or not api_key:
                logger.warning(
                    "Jellyfin %d is partially configured; skipping it.",
                    index,
                )
                index += 1
                continue

            servers.append(
                JellyfinServer(
                    name=f"Jellyfin {index}",
                    url=url,
                    api_key=api_key,
                )
            )

            index += 1

        return tuple(servers)

    async def connect(self) -> None:
        """Create the HTTP session used for Jellyfin requests."""
        if self.session is not None and not self.session.closed:
            return

        timeout = aiohttp.ClientTimeout(total=15)

        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "Aegis-Bot/1.0",
            },
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session is not None and not self.session.closed:
            await self.session.close()

        self.session = None

    @staticmethod
    def _normalize_title(value: str) -> str:
        """Normalize a title for comparison."""
        value = value.lower().strip()

        # Remove a standalone year.
        value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)

        # Replace punctuation with spaces.
        value = re.sub(r"[^a-z0-9]+", " ", value)

        # Collapse whitespace.
        value = re.sub(r"\s+", " ", value).strip()

        return value

    @staticmethod
    def _extract_year(value: str) -> int | None:
        """Extract a four-digit release year from a query."""
        match = re.search(r"\b((?:19|20)\d{2})\b", value)

        if not match:
            return None

        try:
            return int(match.group(1))
        except ValueError:
            return None

    @classmethod
    def _titles_match(cls, query: str, jellyfin_title: str) -> bool:
        """Determine whether a Jellyfin title matches the requested title."""
        normalized_query = cls._normalize_title(query)
        normalized_title = cls._normalize_title(jellyfin_title)

        if not normalized_query or not normalized_title:
            return False

        # Exact normalized match.
        if normalized_query == normalized_title:
            return True

        query_tokens = set(normalized_query.split())
        title_tokens = set(normalized_title.split())

        if not query_tokens or not title_tokens:
            return False

        # Avoid matching unrelated titles simply because one contains
        # the other as a substring.
        return query_tokens.issubset(title_tokens) or title_tokens.issubset(
            query_tokens
        )

    @staticmethod
    def _item_type_for_category(category: str) -> str | None:
        """Map a torrent category to the corresponding Jellyfin item type."""
        normalized = category.strip().lower()

        if normalized == "movies":
            return "Movie"

        if normalized == "series":
            return "Series"

        return None

    async def _search_server(
        self,
        server: JellyfinServer,
        query: str,
        item_type: str,
    ) -> list[dict]:
        """Search one Jellyfin server."""
        await self.connect()

        if self.session is None:
            return []

        headers = {
            "Authorization": f'MediaBrowser Token="{server.api_key}"',
            "Accept": "application/json",
            "User-Agent": "Aegis-Bot/1.0",
        }

        params = {
            "SearchTerm": query,
            "Recursive": "true",
            "IncludeItemTypes": item_type,
            "Fields": "ProductionYear,Path",
            "Limit": "25",
        }

        url = f"{server.url}/Items"

        try:
            async with self.session.get(
                url,
                params=params,
                headers=headers,
            ) as response:
                if response.status != 200:
                    body = await response.text()

                    logger.warning(
                        "%s Jellyfin search returned HTTP %d: %s",
                        server.name,
                        response.status,
                        body[:500],
                    )

                    return []

                data = await response.json()

                items = data.get("Items", [])

                if not isinstance(items, list):
                    return []

                return items

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to search %s at %s",
                server.name,
                server.url,
            )
            return []

    async def is_available(
        self,
        query: str,
        category: str,
    ) -> dict | None:
        """
        Check all configured Jellyfin servers for an existing item.

        Returns:
            A dictionary describing the matching library item, or None.
        """
        item_type = self._item_type_for_category(category)

        # Jellyfin availability checks are currently only relevant for
        # Movies and Series.
        if item_type is None:
            return None

        if not self.servers:
            return None

        await self.connect()

        query_year = self._extract_year(query)

        for server in self.servers:
            items = await self._search_server(
                server,
                query,
                item_type,
            )

            for item in items:
                title = str(item.get("Name") or "").strip()

                if not title:
                    continue

                if not self._titles_match(query, title):
                    continue

                item_year = item.get("ProductionYear")

                if query_year is not None and item_year is not None:
                    try:
                        if int(item_year) != query_year:
                            continue
                    except (TypeError, ValueError):
                        pass

                return {
                    "server": server.name,
                    "url": server.url,
                    "title": title,
                    "year": item_year,
                    "type": item.get("Type") or item_type,
                    "item_id": str(item.get("Id") or "").strip(),
                    "path": str(item.get("Path") or "").strip(),
                }
            async def get_seasons(
        self,
        server_name: str,
        series_id: str,
    ) -> list[dict]:
        """Return the seasons belonging to a Jellyfin series."""
        server = self._get_server(server_name)

        if server is None:
            return []

        await self.connect()

        if self.session is None:
            return []

        headers = {
            "Authorization": f'MediaBrowser Token="{server.api_key}"',
            "Accept": "application/json",
            "User-Agent": "Aegis-Bot/1.0",
        }

        url = f"{server.url}/Shows/{series_id}/Seasons"

        try:
            async with self.session.get(
                url,
                headers=headers,
                params={
                    "Fields": "Path,ProductionYear",
                    "SortBy": "IndexNumber",
                    "SortOrder": "Ascending",
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()

                    logger.warning(
                        "%s Jellyfin seasons request returned HTTP %d: %s",
                        server.name,
                        response.status,
                        body[:500],
                    )

                    return []

                data = await response.json()
                items = data.get("Items", [])

                return items if isinstance(items, list) else []

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to retrieve seasons for Jellyfin item %s",
                series_id,
            )
            return []

    async def get_episodes(
        self,
        server_name: str,
        series_id: str,
        season_number: int,
    ) -> list[dict]:
        """Return all episodes for one Jellyfin season."""
        server = self._get_server(server_name)

        if server is None:
            return []

        await self.connect()

        if self.session is None:
            return []

        headers = {
            "Authorization": f'MediaBrowser Token="{server.api_key}"',
            "Accept": "application/json",
            "User-Agent": "Aegis-Bot/1.0",
        }

        url = f"{server.url}/Shows/{series_id}/Episodes"

        try:
            async with self.session.get(
                url,
                headers=headers,
                params={
                    "Season": season_number,
                    "Fields": "Path,ProductionYear",
                    "SortBy": "ParentIndexNumber,IndexNumber",
                    "SortOrder": "Ascending",
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()

                    logger.warning(
                        "%s Jellyfin episodes request returned HTTP %d: %s",
                        server.name,
                        response.status,
                        body[:500],
                    )

                    return []

                data = await response.json()
                items = data.get("Items", [])

                return items if isinstance(items, list) else []

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Failed to retrieve episodes for Jellyfin series %s season %d",
                series_id,
                season_number,
            )
            return []

    async def download_item(
        self,
        server_name: str,
        item_id: str,
        destination: Path,
    ) -> bool:
        """Download one Jellyfin item to the local filesystem."""
        server = self._get_server(server_name)

        if server is None:
            return False

        await self.connect()

        if self.session is None:
            return False

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = destination.with_name(
            f"{destination.name}.part"
        )

        headers = {
            "Authorization": f'MediaBrowser Token="{server.api_key}"',
            "User-Agent": "Aegis-Bot/1.0",
        }

        url = f"{server.url}/Items/{item_id}/Download"

        try:
            async with self.session.get(
                url,
                headers=headers,
            ) as response:
                if response.status != 200:
                    body = await response.text()

                    logger.warning(
                        "%s Jellyfin download returned HTTP %d: %s",
                        server.name,
                        response.status,
                        body[:500],
                    )

                    return False

                with temporary.open("wb") as file:
                    async for chunk in response.content.iter_chunked(
                        1024 * 1024
                    ):
                        file.write(chunk)

            temporary.replace(destination)

            logger.info(
                "[Jellyfin] Downloaded '%s' from %s to %s.",
                item_id,
                server.name,
                destination,
            )

            return True

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "[Jellyfin] Failed to download item %s from %s.",
                item_id,
                server.name,
            )

            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

            return False

    def _get_server(
    self,
    server_name: str,
    ) -> JellyfinServer | None:
    """Return a configured server by name."""
    normalized = str(server_name).strip().lower()

    for server in self.servers:
        if server.name.strip().lower() == normalized:
            return server

    return None


    async def refresh(self, server_name: str) -> bool:
        """Ask Jellyfin to refresh its library."""
        server = self._get_server(server_name)

        if server is None:
            return False

        await self.connect()

        if self.session is None:
            return False

        headers = {
            "Authorization": f'MediaBrowser Token="{server.api_key}"',
            "User-Agent": "Aegis-Bot/1.0",
        }

        url = f"{server.url}/Library/Refresh"

        try:
            async with self.session.post(
                url,
                headers=headers,
            ) as response:
                if response.status not in {200, 204}:
                    body = await response.text()

                    logger.warning(
                        "%s Jellyfin library refresh returned HTTP %d: %s",
                        server.name,
                        response.status,
                        body[:500],
                    )

                    return False

                logger.info(
                    "[Jellyfin] Requested library refresh on %s.",
                    server.name,
                )

                return True

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "[Jellyfin] Failed to refresh %s.",
                server.name,
            )
            return False


        return None