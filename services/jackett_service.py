"""Jackett Torznab search service."""

import asyncio
import html
import os
import re
import xml.etree.ElementTree as element_tree
from urllib.parse import quote

import aiohttp

from utils.logger import logger


class JackettService:
    """Search selected Jackett indexers and normalize their results."""

    # Used when JACKETT_INDEXERS is not set. "rutracker" is
    # RuTracker.org (semi-private; needs an account in Jackett), not
    # "rutracker-ru", which is the unrelated public site rutracker.ru.
    DEFAULT_INDEXERS = (
        "rutracker",
        "thepiratebay",
        "1337x",
    )

    def __init__(self):
        self.base_url = os.getenv(
            "JACKETT_URL",
            "",
        ).strip().rstrip("/")

        self.api_key = os.getenv(
            "JACKETT_API_KEY",
            "",
        ).strip()

        try:
            timeout = int(
                os.getenv(
                    "JACKETT_TIMEOUT",
                    "120",
                )
            )
        except ValueError:
            timeout = 120

        self.timeout = aiohttp.ClientTimeout(
            total=max(1, timeout)
        )

        configured_indexers = os.getenv(
            "JACKETT_INDEXERS",
            "",
        ).strip()

        if configured_indexers:
            self.indexers = tuple(
                indexer.strip()
                for indexer in configured_indexers.split(",")
                if indexer.strip()
            )
        else:
            self.indexers = self.DEFAULT_INDEXERS

        self.session: aiohttp.ClientSession | None = None

    async def connect(self):
        """Create an HTTP session for Jackett requests."""
        if self.session is not None and not self.session.closed:
            return

        if not self.base_url:
            raise RuntimeError(
                "JACKETT_URL is not configured."
            )

        if not self.api_key:
            raise RuntimeError(
                "JACKETT_API_KEY is not configured."
            )

        self.session = aiohttp.ClientSession(
            timeout=self.timeout,
            headers={
                "User-Agent": "Aegis-Bot/1.0",
            },
        )

    async def close(self):
        """Close the Jackett HTTP session."""
        if (
            self.session is not None
            and not self.session.closed
        ):
            await self.session.close()

        self.session = None

    @staticmethod
    def number(value, default=0):
        """Convert an unknown value to an integer."""
        try:
            return int(float(value))
        except (
            TypeError,
            ValueError,
        ):
            return default

    @staticmethod
    def clean_text(value):
        """Normalize text received from an XML feed."""
        return str(value or "").strip()

    @staticmethod
    def local_name(element):
        """Return an XML tag without its namespace."""
        return element.tag.rsplit(
            "}",
            1,
        )[-1].lower()

    @classmethod
    def child_text(cls, item, name):
        """Return text from an RSS item child element."""
        for child in item:
            if cls.local_name(child) == name:
                return cls.clean_text(child.text)

        return ""

    @classmethod
    def torznab_attributes(cls, item):
        """Return Torznab attributes as a lowercase dictionary."""
        attributes = {}

        for child in item.iter():
            if cls.local_name(child) != "attr":
                continue

            name = cls.clean_text(
                child.attrib.get("name")
            ).lower()

            if not name:
                continue

            attributes[name] = cls.clean_text(
                child.attrib.get("value")
            )

        return attributes

    @classmethod
    def enclosure_url(cls, item):
        """Return the item enclosure URL, when present."""
        for child in item:
            if cls.local_name(child) != "enclosure":
                continue

            url = cls.clean_text(
                child.attrib.get("url")
            )

            if url:
                return url

        return ""

    @classmethod
    def magnet_url(cls, item, attributes, title):
        """Return a usable magnet URL from a Torznab item."""
        candidates = (
            attributes.get("magneturl"),
            attributes.get("magneturi"),
            cls.enclosure_url(item),
            cls.child_text(item, "link"),
        )

        for candidate in candidates:
            candidate = cls.clean_text(candidate)

            if candidate.lower().startswith("magnet:?"):
                return candidate

        info_hash = cls.clean_text(
            attributes.get("infohash")
        )

        if info_hash:
            return (
                "magnet:?xt=urn:btih:"
                f"{info_hash}&dn={quote(title)}"
            )

        return ""

    @classmethod
    def source_url(
        cls,
        item,
        attributes,
        title,
    ):
        """
        Return the best URL for adding the torrent.

        Prefer a magnet when one is available. Otherwise fall back
        to the Jackett-provided torrent download URL.
        """
        magnet = cls.magnet_url(
            item,
            attributes,
            title,
        )

        if magnet:
            return magnet

        enclosure = cls.enclosure_url(item)

        if enclosure:
            return enclosure

        link = cls.child_text(
            item,
            "link",
        )

        if link:
            return link

        return ""

    @classmethod
    def parse_results(
        cls,
        xml_text,
        indexer,
    ):
        """Parse a Jackett Torznab XML response."""
        try:
            root = element_tree.fromstring(xml_text)
        except element_tree.ParseError as ex:
            raise RuntimeError(
                f"Jackett returned invalid XML: {ex}"
            ) from ex

        results = []

        for item in root.iter():
            if cls.local_name(item) != "item":
                continue

            # Some indexers (The Pirate Bay) send titles with HTML
            # entities still in them, such as &rsquo; and &ndash;.
            title = html.unescape(
                cls.child_text(
                    item,
                    "title",
                )
            )

            if not title:
                continue

            attributes = cls.torznab_attributes(
                item
            )

            source_url = cls.source_url(
                item,
                attributes,
                title,
            )

            if not source_url:
                continue

            magnet = cls.magnet_url(
                item,
                attributes,
                title,
            )

            results.append(
                {
                    "title": title,
                    "magnet": magnet,
                    "source_url": source_url,
                    "indexer": indexer,
                    "size": cls.number(
                        cls.child_text(
                            item,
                            "size",
                        )
                    ),
                    "seeders": cls.number(
                        attributes.get(
                            "seeders"
                        )
                    ),
                    "leechers": cls.number(
                        attributes.get(
                            "peers"
                        )
                    ),
                    "grabs": cls.number(
                        attributes.get(
                            "grabs"
                        )
                    ),
                    "category": cls.child_text(
                        item,
                        "category",
                    ),
                    "publish_date": cls.child_text(
                        item,
                        "pubdate",
                    ),
                    "info_hash": cls.clean_text(
                        attributes.get(
                            "infohash"
                        )
                    ).lower(),
                    "uploader": cls.uploader(
                        cls.child_text(
                            item,
                            "description",
                        )
                    ),
                }
            )

        return results

    async def resolve_source_url(
        self,
        source_url: str,
    ) -> str:
        """
        Resolve a Jackett download URL into a magnet URL.

        Jackett may return HTTP 302 redirects whose Location header
        contains a magnet URI. aiohttp cannot follow a redirect from
        HTTP to the magnet scheme, so the redirect must be inspected
        manually.
        """
        source_url = self.clean_text(source_url)

        if not source_url:
            return ""

        if source_url.lower().startswith("magnet:?"):
            return source_url

        await self.connect()

        if self.session is None:
            raise RuntimeError(
                "Jackett HTTP session was not initialized."
            )

        try:
            async with self.session.get(
                source_url,
                allow_redirects=False,
            ) as response:
                location = self.clean_text(
                    response.headers.get("Location")
                )

                if location.lower().startswith("magnet:?"):
                    return location

                if 300 <= response.status < 400:
                    logger.warning(
                        "[JackettService] Download URL returned "
                        "an unsupported redirect for %s: HTTP %s",
                        self._safe_source_label(source_url),
                        response.status,
                    )
                    return ""

                if response.status >= 400:
                    logger.warning(
                        "[JackettService] Download URL returned "
                        "HTTP %s for %s.",
                        response.status,
                        self._safe_source_label(source_url),
                    )
                    return ""

                final_url = str(response.url)

                if final_url.lower().startswith("magnet:?"):
                    return final_url

        except asyncio.TimeoutError:
            logger.warning(
                "[JackettService] Timed out resolving download URL: %s",
                self._safe_source_label(source_url),
            )
        except aiohttp.ClientError as ex:
            logger.warning(
                "[JackettService] Failed to resolve download URL: %s",
                ex,
            )

        return ""

    @staticmethod
    def _safe_source_label(source_url: str) -> str:
        """
        Return a safe representation of a source URL for logging.

        Jackett download URLs contain the API key in their query string,
        so the full URL must never be written to logs.
        """
        try:
            from urllib.parse import urlsplit

            parsed = urlsplit(source_url)

            if parsed.query:
                return (
                    f"{parsed.scheme}://{parsed.netloc}"
                    f"{parsed.path}"
                )

            return source_url
        except Exception:
            return "<jackett-download-url>"

    async def resolve_result(
        self,
        result: dict,
    ) -> dict:
        """
        Resolve a result's source URL into a magnet when necessary.

        Results that already contain a magnet are returned unchanged.
        """
        magnet = self.clean_text(
            result.get("magnet")
        )

        if magnet.lower().startswith("magnet:?"):
            result["source_url"] = magnet
            return result

        source_url = self.clean_text(
            result.get("source_url")
        )

        if not source_url:
            return result

        resolved_magnet = await self.resolve_source_url(
            source_url
        )

        if resolved_magnet:
            result["magnet"] = resolved_magnet
            result["source_url"] = resolved_magnet

        return result

    async def search_indexer(
        self,
        indexer,
        query,
    ):
        """Search one configured Jackett indexer."""
        await self.connect()

        if self.session is None:
            raise RuntimeError(
                "Jackett HTTP session was not initialized."
            )

        url = (
            f"{self.base_url}/api/v2.0/indexers/"
            f"{indexer}/results/torznab/api"
        )

        params = {
            "apikey": self.api_key,
            "t": "search",
            "q": query,
        }

        async with self.session.get(
            url,
            params=params,
        ) as response:
            body = await response.text()

            if response.status >= 400:
                raise RuntimeError(
                    f"HTTP {response.status}: "
                    f"{body[:200]}"
                )

        return self.parse_results(
            body,
            indexer,
        )

    @staticmethod
    def uploader(description):
        """
        The uploader's name from a Jackett description.

        RuTracker.org, The Pirate Bay and 1337x put it there as
        "Uploader: name", sometimes followed by <br> and more text.
        Jackett passes no reputation, only the name.
        """
        match = re.search(
            r"Uploader:\s*([^<\r\n]+)",
            html.unescape(str(description or "")),
        )

        return match.group(1).strip() if match else ""

    @staticmethod
    def simplified_query(query):
        """
        The query without punctuation, for a second try.

        Indexers such as rutracker match words literally: "Belmont's"
        with a straight apostrophe does not find a title written
        "Belmont’s", and a colon can break the search too. Possessive
        's is dropped and other punctuation becomes a space; letters
        in any script are kept.
        """
        text = re.sub(r"['’`´]s\b", "", str(query))
        text = re.sub(r"[^\w]+", " ", text)
        return " ".join(text.split())

    @staticmethod
    def duplicate_key(result):
        """Build a stable key for cross-indexer duplicates."""
        info_hash = result.get("info_hash")

        if info_hash:
            return f"hash:{info_hash}"

        normalized_title = re.sub(
            r"[^a-z0-9]+",
            "",
            str(
                result.get("title", "")
            ).lower(),
        )

        return (
            f"title:{normalized_title}:"
            f"{result.get('size', 0)}"
        )

    async def search(self, query):
        """
        Search every enabled indexer.

        Returns a tuple of:
        - deduplicated torrent-capable results
        - errors keyed by indexer ID
        """
        tasks = [
            self.search_indexer(
                indexer,
                query,
            )
            for indexer in self.indexers
        ]

        responses = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        # An indexer that found nothing for the exact query gets one
        # more try without punctuation, even when others found
        # something: otherwise its releases never show up.
        simplified = self.simplified_query(query)

        if simplified and simplified != query.strip():
            empty = [
                position
                for position, response in enumerate(responses)
                if not isinstance(response, Exception) and not response
            ]

            if empty:
                retries = await asyncio.gather(
                    *(
                        self.search_indexer(self.indexers[position], simplified)
                        for position in empty
                    ),
                    return_exceptions=True,
                )

                for position, retry in zip(empty, retries):
                    if isinstance(retry, Exception) or not retry:
                        continue

                    logger.info(
                        "[JackettService] %s found %d result(s) for "
                        "'%s' after nothing for '%s'.",
                        self.indexers[position],
                        len(retry),
                        simplified,
                        query,
                    )
                    responses[position] = retry

        results = []
        errors = {}

        for indexer, response in zip(
            self.indexers,
            responses,
        ):
            if isinstance(
                response,
                Exception,
            ):
                errors[indexer] = str(
                    response
                )

                logger.warning(
                    "[JackettService] Search failed for %s: %s",
                    indexer,
                    response,
                )

                continue

            results.extend(
                response
            )

        deduplicated = {}

        for result in results:
            key = self.duplicate_key(
                result
            )

            existing = deduplicated.get(
                key
            )

            if existing is None:
                deduplicated[key] = result
                continue

            # Preserve the healthier version of the same release.
            existing_seeders = self.number(
                existing.get(
                    "seeders"
                )
            )

            result_seeders = self.number(
                result.get(
                    "seeders"
                )
            )

            if result_seeders > existing_seeders:
                deduplicated[key] = result

        # IMPORTANT:
        # Do not resolve HTTP source URLs here.
        #
        # Some indexers, such as 1337x, return an HTTP Jackett
        # download URL which redirects to a magnet. Resolving every
        # result here makes searches unnecessarily slow.
        #
        # The selected result is resolved by TorrentSearchView when
        # the user actually chooses it.

        return list(
            deduplicated.values()
        ), errors