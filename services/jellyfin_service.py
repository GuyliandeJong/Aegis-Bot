"""Jellyfin availability and download service for torrent searches."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

import aiohttp

from utils.logger import logger


@dataclass(frozen=True)
class JellyfinServer:
    name: str
    url: str
    api_key: str

    # The first configured server is the library that downloads from
    # the other servers are copied into.
    primary: bool = False

    # Jellyfin user whose watched state /jellyfin movies shows
    # (JELLYFIN_<n>_USER). Empty: watched state is not shown.
    user: str = ""


class JellyfinDownloadError(Exception):
    """A download failed; the message is suitable for showing in Discord."""


class JellyfinService:
    """Find movies and series on Jellyfin servers and copy them between them."""

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

            # An optional display name, e.g. JELLYFIN_1_NAME=Home, tells
            # users which library a title was found in.
            name = os.getenv(f"JELLYFIN_{index}_NAME", "").strip()

            servers.append(
                JellyfinServer(
                    name=name or f"Jellyfin {index}",
                    url=url,
                    api_key=api_key,
                    primary=index == 1,
                    user=os.getenv(f"JELLYFIN_{index}_USER", "").strip(),
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

        # A shortened query may match a longer library title, but never
        # the other way round: "The Last of Us" must not match "Us".
        if not query_tokens.issubset(title_tokens):
            return False

        # The query must also cover most of the title, so "Alien" does
        # not claim "Alien: Romulus" while "The Office" still finds
        # "The Office (US)". A miss only costs a normal torrent search.
        return len(query_tokens) / len(title_tokens) >= 0.6

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
            "Fields": "ProductionYear,Path,MediaStreams,MediaSources",
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
        matches = await self.find_matches(
            query,
            category,
        )

        return matches[0] if matches else None

    async def find_matches(
        self,
        query: str,
        category: str,
    ) -> list[dict]:
        """
        Check every configured Jellyfin server for an existing item.

        Returns:
            One dictionary per server that has the item, in the order
            the servers are configured. Empty when none has it.
        """
        item_type = self._item_type_for_category(category)

        # Jellyfin availability checks are currently only relevant for
        # Movies and Series.
        if item_type is None:
            return []

        if not self.servers:
            return []

        await self.connect()

        # The servers are independent, so ask them at the same time.
        results = await asyncio.gather(
            *(
                self._match_on_server(
                    server,
                    query,
                    item_type,
                )
                for server in self.servers
            )
        )

        return [
            result
            for result in results
            if result is not None
        ]

    async def _match_on_server(
        self,
        server: JellyfinServer,
        query: str,
        item_type: str,
    ) -> dict | None:
        """Return the first matching library item on one server."""
        query_year = self._extract_year(query)

        # Jellyfin searches titles only, so a year typed after the
        # title ("Dune 2021") would otherwise find nothing. The year
        # is still compared against each result below.
        search_term = re.sub(
            r"\b(?:19|20)\d{2}\b",
            " ",
            query,
        ).strip() or query

        items = await self._search_server(
            server,
            search_term,
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
                "primary": server.primary,
                "url": server.url,
                "title": title,
                "year": item_year,
                "type": item.get("Type") or item_type,
                "item_id": str(item.get("Id") or "").strip(),
                "path": str(item.get("Path") or "").strip(),
                "media_source_id": self.media_source_id(item),
                "subtitles": self.subtitle_info(item),
                "quality": self.video_quality(item),
            }

        return None

    # ------------------------------------------------------------
    # Downloading from another Jellyfin server
    #
    # The first configured server (JELLYFIN_1_*) is the primary
    # library. Titles found only on another server can be copied
    # into the primary library's folders.
    # ------------------------------------------------------------

    DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024

    @staticmethod
    def library_dir(category: str) -> Path | None:
        """
        Return the primary library folder for a category.

        The folders come from JELLYFIN_MOVIES_DIR and
        JELLYFIN_SERIES_DIR, as seen from inside the container.
        Returns None when the folder is not configured, which
        disables downloading for that category.
        """
        variable = {
            "movies": "JELLYFIN_MOVIES_DIR",
            "series": "JELLYFIN_SERIES_DIR",
        }.get(category.strip().lower())

        if variable is None:
            return None

        value = os.getenv(variable, "").strip()

        return Path(value) if value else None

    def primary_server(self) -> JellyfinServer | None:
        """Return the primary Jellyfin server, when configured."""
        for server in self.servers:
            if server.primary:
                return server

        return None

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

    @staticmethod
    def _auth_headers(server: JellyfinServer) -> dict:
        return {
            "Authorization": f'MediaBrowser Token="{server.api_key}"',
            "User-Agent": "Aegis-Bot/1.0",
        }

    @staticmethod
    def safe_name(value) -> str:
        """
        Make a title or file name safe to use as one path component.

        Names come from another person's server, so anything that
        could change the destination folder is removed.
        """
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(value or ""))
        value = re.sub(r"\s+", " ", value)

        return value.strip(" .")

    @staticmethod
    def _match_owner(path: Path, reference: Path) -> None:
        """
        Give a new file or folder the same owner as an existing one.

        The bot usually runs as root inside its container. Without
        this, downloads would be owned by root and could not be renamed
        or deleted by the library's normal user.
        """
        if not hasattr(os, "chown"):
            return

        try:
            owner = reference.stat()
            os.chown(path, owner.st_uid, owner.st_gid)

        except OSError as ex:
            logger.warning(
                "[Jellyfin] Could not set the owner of %s: %s",
                path,
                ex,
            )

    @classmethod
    def _create_directory(cls, directory: Path) -> None:
        """
        Create a library folder, including missing parent folders.

        Every folder that had to be created gets the owner of the
        closest folder that already existed.
        """
        created = []
        existing = directory

        while not existing.exists():
            created.append(existing)
            existing = existing.parent

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        for path in reversed(created):
            cls._match_owner(path, existing)

    @classmethod
    def media_filename(cls, item: dict) -> str:
        """
        Return the original media file name of a Jellyfin item.

        Only the final component of the reported path is used; the
        other server may run on Windows or Linux.
        """
        path = str(item.get("Path") or item.get("path") or "")
        name = re.split(r"[\\/]", path)[-1]

        return cls.safe_name(name)

    @classmethod
    def _filename_from_response(
        cls,
        response: aiohttp.ClientResponse,
    ) -> str:
        """Return the file name offered by a download response."""
        disposition = response.headers.get("Content-Disposition", "")

        match = re.search(
            r"filename\*=(?:UTF-8'')?([^;]+)",
            disposition,
            re.IGNORECASE,
        )

        if match:
            name = unquote(match.group(1).strip().strip('"'))
        else:
            match = re.search(
                r'filename="?([^";]+)"?',
                disposition,
                re.IGNORECASE,
            )
            name = match.group(1) if match else ""

        return cls.safe_name(re.split(r"[\\/]", name)[-1])

    # ------------------------------------------------------------
    # Subtitles
    # ------------------------------------------------------------

    # Jellyfin reports three-letter language codes. Users and file
    # names commonly use two-letter codes or full names.
    _LANGUAGES = {
        "eng": ("English", "en", {"en", "eng", "english"}),
        "dut": ("Dutch", "nl", {"nl", "dut", "nld", "dutch"}),
        "ger": ("German", "de", {"de", "ger", "deu", "german"}),
        "fre": ("French", "fr", {"fr", "fre", "fra", "french"}),
        "spa": ("Spanish", "es", {"es", "spa", "spanish"}),
    }

    @classmethod
    def language_code(cls, value) -> str:
        """Normalize a language name or code to Jellyfin's form."""
        value = str(value or "").strip().lower()

        for code, (_, _, aliases) in cls._LANGUAGES.items():
            if value in aliases:
                return code

        return value

    @classmethod
    def language_name(cls, code: str) -> str:
        """Return a readable name for a language code."""
        if code in cls._LANGUAGES:
            return cls._LANGUAGES[code][0]

        return code.upper() if code else "Unknown"

    @classmethod
    def preferred_subtitle_language(cls) -> str:
        """Return the preferred subtitle language (SUBTITLE_LANGUAGE)."""
        return cls.language_code(
            os.getenv("SUBTITLE_LANGUAGE", "eng")
        ) or "eng"

    @staticmethod
    def media_source_id(item: dict) -> str:
        """Return the id Jellyfin uses for an item's media file."""
        sources = item.get("MediaSources")

        if isinstance(sources, list) and sources:
            source_id = str(sources[0].get("Id") or "").strip()

            if source_id:
                return source_id

        return str(item.get("Id") or "").strip()

    # Jellyfin codec names, as the torrent preference keys spell them.
    _CODEC_NAMES = {
        "hevc": ("HEVC", ("hevc", "h.265", "x265")),
        "h265": ("HEVC", ("hevc", "h.265", "x265")),
        "h264": ("H.264", ("h.264", "x264")),
        "avc": ("H.264", ("h.264", "x264")),
        "av1": ("AV1", ("av1",)),
        "vp9": ("VP9", ("vp9",)),
        "mpeg2video": ("MPEG-2", ("mpeg2",)),
    }

    @staticmethod
    def _resolution_label(width, height) -> str | None:
        """Name a video size the way release names do (1080p, 2160p)."""
        try:
            width = int(width or 0)
            height = int(height or 0)
        except (TypeError, ValueError):
            return None

        if not width and not height:
            return None

        # Width decides for wide films: 1920x800 is still 1080p.
        if width >= 3200 or height >= 1600:
            return "2160p"

        if width >= 1800 or height >= 1000:
            return "1080p"

        if width >= 1200 or height >= 700:
            return "720p"

        if width >= 900 or height >= 540:
            return "576p"

        return "480p"

    @classmethod
    def video_quality(cls, item: dict) -> dict | None:
        """
        Describe the video of an item: resolution, codec and size.

        Returns None when Jellyfin sent no stream information.
        `codec_keys` are the spellings used in torrent_preferences.json.
        """
        streams = item.get("MediaStreams")

        if not isinstance(streams, list):
            return None

        video = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict)
                and stream.get("Type") == "Video"
            ),
            None,
        )

        if video is None:
            return None

        codec = str(video.get("Codec") or "").strip().lower()
        name, keys = cls._CODEC_NAMES.get(
            codec,
            (codec.upper() or None, (codec,) if codec else ()),
        )

        size = None
        sources = item.get("MediaSources")

        if isinstance(sources, list) and sources:
            try:
                size = int(sources[0].get("Size") or 0) or None
            except (TypeError, ValueError, AttributeError):
                size = None

        return {
            "resolution": cls._resolution_label(
                video.get("Width"),
                video.get("Height"),
            ),
            "codec": name,
            "codec_keys": list(keys),
            "size": size,
        }

    @classmethod
    def subtitle_info(cls, item: dict) -> dict:
        """
        Summarize the subtitles Jellyfin reports for an item.

        Returns a dictionary with:
            known:     False when Jellyfin sent no stream information
            preferred: "embedded", "external" or None for the
                       preferred language
            external:  stream indexes of separate text subtitle files
                       in the preferred language
            others:    names of the other subtitle languages present
        """
        streams = item.get("MediaStreams")

        info = {
            "known": isinstance(streams, list),
            "preferred": None,
            "external": [],
            "others": [],
        }

        if not info["known"]:
            return info

        wanted = cls.preferred_subtitle_language()
        others = set()

        for stream in streams:
            if not isinstance(stream, dict):
                continue

            if stream.get("Type") != "Subtitle":
                continue

            language = cls.language_code(stream.get("Language"))

            if language != wanted:
                if language:
                    others.add(cls.language_name(language))
                continue

            if not stream.get("IsExternal"):
                info["preferred"] = "embedded"
                continue

            if info["preferred"] is None:
                info["preferred"] = "external"

            # Only text subtitles can be saved as an .srt file.
            if (
                stream.get("IsTextSubtitleStream")
                and isinstance(stream.get("Index"), int)
            ):
                info["external"].append(stream["Index"])

        info["others"] = sorted(others)

        return info

    @classmethod
    def describe_subtitles(cls, info: dict | None) -> str | None:
        """Describe subtitle availability for one item, for Discord."""
        if not info or not info.get("known"):
            return None

        name = cls.language_name(cls.preferred_subtitle_language())

        if info["preferred"] == "embedded":
            return f"Subtitles: {name} (in the file)"

        if info["preferred"] == "external":
            return f"Subtitles: {name} (separate file)"

        if info["others"]:
            return (
                f"Subtitles: no {name} "
                f"(has {', '.join(info['others'])})"
            )

        return "Subtitles: none"

    @classmethod
    def subtitle_filename(cls, video_filename: str) -> str:
        """Return the name for a subtitle file next to a video file."""
        language = cls.preferred_subtitle_language()

        suffix = (
            cls._LANGUAGES[language][1]
            if language in cls._LANGUAGES
            else language
        )

        return f"{Path(video_filename).stem}.{suffix}.srt"

    async def download_subtitle(
        self,
        server_name: str,
        item_id: str,
        media_source_id: str,
        stream_index: int,
        destination: Path,
    ) -> bool:
        """
        Save one subtitle stream as an .srt file.

        Subtitles are a bonus: any failure is logged and reported as
        False, and never interrupts the media download.
        """
        server = self._get_server(server_name)

        if server is None:
            return False

        try:
            if destination.exists():
                return True

            await self.connect()

            async with self.session.get(
                f"{server.url}/Videos/{item_id}/"
                f"{media_source_id or item_id}/Subtitles/"
                f"{stream_index}/Stream.srt",
                headers=self._auth_headers(server),
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "%s returned HTTP %d for subtitle %s of item %s.",
                        server.name,
                        response.status,
                        stream_index,
                        item_id,
                    )
                    return False

                content = await response.read()

            if not content.strip():
                return False

            temporary = destination.with_name(
                f"{destination.name}.part"
            )

            await asyncio.to_thread(
                temporary.write_bytes,
                content,
            )

            await asyncio.to_thread(
                temporary.replace,
                destination,
            )

            await asyncio.to_thread(
                self._match_owner,
                destination,
                destination.parent,
            )

            logger.info(
                "[Jellyfin] Saved subtitle %s.",
                destination,
            )

            return True

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "[Jellyfin] Failed to save subtitle %s of item %s.",
                stream_index,
                item_id,
            )
            return False

    # ------------------------------------------------------------
    # Subtitles from the server's providers (OpenSubtitles plugin)
    # ------------------------------------------------------------

    async def _request_json(
        self,
        server_name: str,
        method: str,
        path: str,
        timeout: float = 90,
    ) -> tuple[int, object]:
        """
        One API call: (HTTP status, parsed JSON or None).

        Returns (0, None) when the server could not be reached.
        Provider searches go out to the internet, hence the longer
        default timeout.
        """
        server = self._get_server(server_name)

        if server is None:
            return 0, None

        await self.connect()

        if self.session is None:
            return 0, None

        try:
            async with self.session.request(
                method,
                f"{server.url}{path}",
                headers={
                    **self._auth_headers(server),
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                body = await response.text()

                if response.status >= 400:
                    logger.warning(
                        "%s Jellyfin %s %s returned HTTP %d: %s",
                        server.name,
                        method,
                        path,
                        response.status,
                        body[:300],
                    )

                try:
                    data = json.loads(body) if body.strip() else None
                except ValueError:
                    data = None

                return response.status, data

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Jellyfin %s %s failed on %s",
                method,
                path,
                server.name,
            )
            return 0, None

    async def get_item(self, server_name: str, item_id: str) -> dict | None:
        """One item with its streams, as Jellyfin sees it now."""
        items = await self._get_items(
            server_name,
            "/Items",
            {
                "Ids": item_id,
                "Fields": "MediaStreams,ProductionYear,Path",
                "EnableImages": "false",
                "EnableUserData": "false",
            },
        )

        return items[0] if items else None

    async def list_movies(self, server_name: str, item_type: str = "Movie") -> list[dict]:
        """
        Every movie (or, with item_type="Episode", every episode) with
        its streams, newest addition first.
        """
        return await self._get_items(
            server_name,
            "/Items",
            {
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "IsMissing": "false",
                "Fields": "MediaStreams,ProductionYear,Path,DateCreated",
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "EnableImages": "false",
                "EnableUserData": "false",
            },
            timeout=180,
        )

    @staticmethod
    def date_created(item: dict) -> float | None:
        """
        An item's DateCreated as a Unix time, or None.

        Jellyfin writes UTC with seven decimals ("2026-10-15T12:34:56.1234567Z");
        the decimals are dropped, a time without zone is taken as UTC.
        """
        value = str(item.get("DateCreated") or "").strip()

        if not value:
            return None

        value = re.sub(r"\.\d+", "", value).replace("Z", "+00:00")

        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None

        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)

        return moment.timestamp()

    # Paging for list_added_since(): items per request, and a ceiling
    # so a freshly imported library does not mean hundreds of requests.
    ADDED_PAGE = 200
    ADDED_MAX = 2000

    async def list_added_since(
        self,
        server_name: str,
        since: float,
    ) -> list[dict] | None:
        """
        Movies and episodes added to a server since a Unix time, newest
        first. None when the server did not answer.

        /Items has no filter on the date an item was added (only
        minDateLastSaved, which also moves when metadata is refreshed),
        so the list is sorted by DateCreated and read page by page until
        an older item appears. SeriesName, ParentIndexNumber, IndexNumber
        and ProductionYear are always in the answer; DateCreated only
        when asked for in Fields.
        """
        added: list[dict] = []
        start = 0

        while start < self.ADDED_MAX:
            query = urlencode(
                {
                    "IncludeItemTypes": "Movie,Episode",
                    "Recursive": "true",
                    "IsMissing": "false",
                    "Fields": "DateCreated",
                    "SortBy": "DateCreated",
                    "SortOrder": "Descending",
                    "StartIndex": start,
                    "Limit": self.ADDED_PAGE,
                    "EnableImages": "false",
                    "EnableUserData": "false",
                    "EnableTotalRecordCount": "false",
                }
            )

            status, data = await self._request_json(
                server_name, "GET", f"/Items?{query}", timeout=30
            )

            if status != 200 or not isinstance(data, dict):
                return None

            page = [
                item for item in data.get("Items") or [] if isinstance(item, dict)
            ]

            for item in page:
                created = self.date_created(item)

                if created is None:
                    continue

                if created < since:
                    return added

                added.append(item)

            if len(page) < self.ADDED_PAGE:
                return added

            start += self.ADDED_PAGE

        logger.warning(
            "[Jellyfin] More than %d items added to %s; the list stops there.",
            self.ADDED_MAX,
            server_name,
        )

        return added

    async def find_user_id(self, server_name: str, username: str) -> str | None:
        """The id of a Jellyfin user by name, ignoring case."""
        status, data = await self._request_json(server_name, "GET", "/Users", timeout=15)

        if status != 200 or not isinstance(data, list):
            return None

        wanted = username.strip().casefold()

        for user in data:
            if isinstance(user, dict) and str(user.get("Name") or "").casefold() == wanted:
                return str(user.get("Id") or "") or None

        return None

    async def list_movies_watched(self, server_name: str) -> tuple[list[dict], bool]:
        """
        Every movie on a server, and whether watched state is included.

        With JELLYFIN_<n>_USER set, the list comes from that user's view
        and each movie carries UserData.Played. Returns ([], False) when
        the server did not answer.
        """
        server = self._get_server(server_name)

        if server is None:
            return [], False

        user_id = None

        if server.user:
            user_id = await self.find_user_id(server_name, server.user)

            if user_id is None:
                logger.warning(
                    "[Jellyfin] User '%s' not found on %s; watched state "
                    "is not shown for it.",
                    server.user,
                    server.name,
                )

        params = {
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "IsMissing": "false",
            "Fields": "ProductionYear,ProviderIds",
            "EnableImages": "false",
            "EnableUserData": "true" if user_id else "false",
        }
        path = f"/Users/{quote(user_id, safe='')}/Items" if user_id else "/Items"

        return await self._get_items(server_name, path, params, timeout=180), user_id is not None

    async def search_remote_subtitles(
        self,
        server_name: str,
        item_id: str,
        language: str,
    ) -> list[dict] | None:
        """
        Ask the server's subtitle providers for one item and language.

        Returns None when the search itself failed, [] when nothing
        was found.
        """
        status, data = await self._request_json(
            server_name,
            "GET",
            f"/Items/{quote(item_id, safe='')}/RemoteSearch/Subtitles/"
            f"{quote(language, safe='')}",
        )

        if status != 200 or not isinstance(data, list):
            return None

        return [entry for entry in data if isinstance(entry, dict)]

    async def download_remote_subtitle(
        self,
        server_name: str,
        item_id: str,
        subtitle_id: str,
    ) -> bool:
        """
        Ask the server to download one search result next to the item.

        Jellyfin answers 204 even when the provider refused (a daily
        limit, for example), so True only means the request was
        accepted; check the item's streams afterwards.
        """
        status, _ = await self._request_json(
            server_name,
            "POST",
            f"/Items/{quote(item_id, safe='')}/RemoteSearch/Subtitles/"
            f"{quote(subtitle_id, safe='')}",
        )

        return status in (200, 204)

    async def _get_items(
        self,
        server_name: str,
        path: str,
        params: dict,
        timeout: float | None = None,
    ) -> list[dict]:
        """
        Return the Items list of one Jellyfin API response.

        `timeout` overrides the session's 15 seconds, for requests that
        list a whole library.
        """
        server = self._get_server(server_name)

        if server is None:
            return []

        await self.connect()

        if self.session is None:
            return []

        try:
            async with self.session.get(
                f"{server.url}{path}",
                headers={
                    **self._auth_headers(server),
                    "Accept": "application/json",
                },
                params=params,
                **(
                    {"timeout": aiohttp.ClientTimeout(total=timeout)}
                    if timeout
                    else {}
                ),
            ) as response:
                if response.status != 200:
                    body = await response.text()

                    logger.warning(
                        "%s Jellyfin request %s returned HTTP %d: %s",
                        server.name,
                        path,
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
                "Jellyfin request %s failed on %s",
                path,
                server.name,
            )
            return []

    async def get_seasons(
        self,
        server_name: str,
        series_id: str,
    ) -> list[dict]:
        """Return the seasons belonging to a Jellyfin series."""
        return await self._get_items(
            server_name,
            f"/Shows/{series_id}/Seasons",
            {
                # Leave out seasons Jellyfin only lists as "missing".
                "IsMissing": "false",
                "SortBy": "IndexNumber",
                "SortOrder": "Ascending",
            },
        )

    async def get_episodes(
        self,
        server_name: str,
        series_id: str,
        season_id: str,
    ) -> list[dict]:
        """Return all episodes of one Jellyfin season, with file paths."""
        return await self._get_items(
            server_name,
            f"/Shows/{series_id}/Episodes",
            {
                "SeasonId": season_id,
                "IsMissing": "false",
                "Fields": "Path,MediaStreams,MediaSources",
                "SortBy": "IndexNumber",
                "SortOrder": "Ascending",
            },
        )

    async def get_series_episodes(
        self,
        server_name: str,
        series_id: str,
    ) -> list[dict]:
        """
        Return every episode of a series, all seasons in one request.

        Each episode carries ParentIndexNumber (its season number),
        IndexNumber, SeasonId and SeasonName, which is enough to tell
        which episodes one library lacks compared to another.
        """
        return await self._get_items(
            server_name,
            f"/Shows/{series_id}/Episodes",
            {
                "IsMissing": "false",
                "Fields": "Path,MediaStreams,MediaSources",
                "SortBy": "ParentIndexNumber,IndexNumber",
                "SortOrder": "Ascending",
            },
            timeout=60,
        )

    async def list_library(
        self,
        server_name: str,
        item_type: str,
    ) -> list[dict]:
        """
        Return every item of one type on a server, with few fields.

        Used by the daily new-content check: one request lists all
        series, one all episodes, instead of one request per series.
        """
        return await self._get_items(
            server_name,
            "/Items",
            {
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "IsMissing": "false",
                "Fields": "ProductionYear,ProviderIds,Path",
                "EnableImages": "false",
                "EnableUserData": "false",
            },
            timeout=180,
        )

    async def download_item(
        self,
        server_name: str,
        item_id: str,
        directory: Path,
        filename: str = "",
        on_progress=None,
    ) -> tuple[Path, bool]:
        """
        Download one Jellyfin item into a folder.

        The file is streamed to a ".part" file and only renamed once
        it is complete, so a failed download never leaves a broken
        media file behind.

        Returns:
            The destination path, and whether a download happened.
            An existing file is left untouched and reported as False.

        Raises:
            JellyfinDownloadError: with a message suitable for Discord.
        """
        server = self._get_server(server_name)

        if server is None:
            raise JellyfinDownloadError(
                "That Jellyfin server is no longer configured."
            )

        filename = self.safe_name(filename)

        if filename and (directory / filename).exists():
            return directory / filename, False

        await self.connect()

        if self.session is None:
            raise JellyfinDownloadError(
                "The Jellyfin connection could not be opened."
            )

        temporary: Path | None = None
        completed = False

        try:
            # The session's 15-second total timeout suits API calls but
            # would abort any real media download, so only guard against
            # a stalled connection here.
            async with self.session.get(
                f"{server.url}/Items/{item_id}/Download",
                headers=self._auth_headers(server),
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=15,
                    sock_read=60,
                ),
            ) as response:
                if response.status in {401, 403}:
                    raise JellyfinDownloadError(
                        f"{server.name} refused the download "
                        f"(HTTP {response.status}). Its API key must "
                        "be allowed to download media."
                    )

                if response.status != 200:
                    raise JellyfinDownloadError(
                        f"{server.name} returned HTTP "
                        f"{response.status} for the download."
                    )

                if not filename:
                    filename = self._filename_from_response(response)

                if not filename:
                    raise JellyfinDownloadError(
                        f"{server.name} did not report a file name "
                        "for this item."
                    )

                destination = directory / filename

                if destination.exists():
                    return destination, False

                try:
                    await asyncio.to_thread(
                        self._create_directory,
                        directory,
                    )
                except OSError as ex:
                    raise JellyfinDownloadError(
                        f"Cannot create `{directory}` "
                        f"({ex.strerror or ex}). Is the library "
                        "mounted read-write in the container?"
                    ) from ex

                total = int(
                    response.headers.get("Content-Length") or 0
                )

                if total:
                    usage = await asyncio.to_thread(
                        shutil.disk_usage,
                        directory,
                    )

                    if total > usage.free:
                        raise JellyfinDownloadError(
                            "There is not enough free space in "
                            f"`{directory}` for this file."
                        )

                temporary = destination.with_name(
                    f"{destination.name}.part"
                )

                # Disk writes run in a worker thread so a slow disk
                # does not stall the rest of the bot.
                file = await asyncio.to_thread(
                    temporary.open,
                    "wb",
                )

                received = 0

                try:
                    async for chunk in response.content.iter_chunked(
                        self.DOWNLOAD_CHUNK_SIZE
                    ):
                        await asyncio.to_thread(
                            file.write,
                            chunk,
                        )

                        received += len(chunk)

                        if on_progress is not None:
                            on_progress(received, total)

                finally:
                    await asyncio.to_thread(file.close)

                if total and received != total:
                    raise JellyfinDownloadError(
                        "The download ended before the whole file "
                        "was received."
                    )

            await asyncio.to_thread(
                temporary.replace,
                destination,
            )

            completed = True

            await asyncio.to_thread(
                self._match_owner,
                destination,
                directory,
            )

            logger.info(
                "[Jellyfin] Downloaded item %s from %s to %s.",
                item_id,
                server.name,
                destination,
            )

            return destination, True

        except JellyfinDownloadError:
            raise

        except asyncio.CancelledError:
            raise

        except OSError as ex:
            logger.exception(
                "[Jellyfin] Could not write item %s.",
                item_id,
            )

            raise JellyfinDownloadError(
                f"Could not write the file ({ex.strerror or ex}). "
                "Is the library mounted read-write in the container?"
            ) from ex

        except Exception as ex:
            logger.exception(
                "[Jellyfin] Failed to download item %s from %s.",
                item_id,
                server.name,
            )

            raise JellyfinDownloadError(
                f"The connection to {server.name} failed during "
                "the download."
            ) from ex

        finally:
            if temporary is not None and not completed:
                try:
                    await asyncio.to_thread(
                        temporary.unlink,
                        missing_ok=True,
                    )
                except OSError:
                    pass

    async def refresh(self, server_name: str) -> bool:
        """Ask Jellyfin to refresh its library."""
        server = self._get_server(server_name)

        if server is None:
            return False

        await self.connect()

        if self.session is None:
            return False

        try:
            async with self.session.post(
                f"{server.url}/Library/Refresh",
                headers=self._auth_headers(server),
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
