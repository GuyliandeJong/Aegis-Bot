"""Turns a Google share link into a title, year and media type."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import aiohttp

from utils.logger import logger


class ShareLinkError(Exception):
    """A link could not be used; the message is suitable for Discord."""


class ShareLinkService:
    """
    Resolves links created with Google's "Share" button.

    A share link redirects to a Google search address that contains
    the title and Google's identifier for the subject. Wikidata is
    then asked what that identifier is, which gives the release year
    and whether it is a film or a series. The Google results page
    itself is never downloaded.
    """

    SHARE_HOST = "share.google"

    # Redirects are only followed between these hosts, so a link can
    # never make the bot request an arbitrary address.
    ALLOWED_HOSTS = {
        "share.google",
        "google.com",
        "www.google.com",
    }

    MAX_REDIRECTS = 4

    WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

    # One query returns the title, the types and the dates, so each
    # link costs Wikidata a single request.
    WIKIDATA_QUERY = (
        "SELECT ?itemLabel ?type ?date ?start WHERE {{ "
        '?item wdt:{prop} "{id}". '
        "OPTIONAL {{ ?item wdt:P31 ?type }} "
        "OPTIONAL {{ ?item wdt:P577 ?date }} "
        "OPTIONAL {{ ?item wdt:P580 ?start }} "
        "SERVICE wikibase:label {{ "
        'bd:serviceParam wikibase:language "en". }} '
        "}} LIMIT 100"
    )

    # Wikidata "instance of" values.
    FILM_TYPES = {
        "Q11424",     # film
        "Q24862",     # short film
        "Q202866",    # animated film
        "Q29168811",  # animated feature film
        "Q506240",    # television film
    }

    SERIES_TYPES = {
        "Q5398426",   # television series
        "Q581714",    # animated series
        "Q1259759",   # miniseries
        "Q63952888",  # anime television series
        "Q117467246",  # animated television series
    }

    HEADERS = {
        "User-Agent": "Aegis-Bot/1.0 (self-hosted Discord bot)",
    }

    @classmethod
    def is_link(cls, text: str) -> bool:
        """Return whether a search query is a web address."""
        return bool(
            re.match(
                r"^https?://\S+$",
                str(text or "").strip(),
                re.IGNORECASE,
            )
        )

    @classmethod
    def _is_share_link(cls, url: str) -> bool:
        parts = urlsplit(url.strip())

        return (
            parts.scheme == "https"
            and (parts.hostname or "").lower() == cls.SHARE_HOST
            and len(parts.path) > 1
        )

    async def resolve(self, url: str) -> dict:
        """
        Resolve a share link.

        Returns:
            {"title": str, "year": int | None, "kind": "Movies",
             "Series" or None}

        Raises:
            ShareLinkError: when the link cannot be used.
        """
        url = str(url or "").strip()

        if not self._is_share_link(url):
            raise ShareLinkError(
                "Only Google share links (`https://share.google/…`) "
                "can be used as a search. Type the title instead."
            )

        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=self.HEADERS,
            ) as session:
                title, knowledge_id = await self._follow(
                    session,
                    url,
                )

                year, kind, label = None, None, None

                if knowledge_id:
                    year, kind, label = await self._lookup(
                        session,
                        knowledge_id,
                    )

        except ShareLinkError:
            raise

        except Exception as ex:
            logger.exception(
                "[ShareLink] Failed to resolve a share link: %s",
                ex,
            )

            raise ShareLinkError(
                "The share link could not be opened. "
                "Type the title instead."
            ) from ex

        title = title or label

        if not title:
            raise ShareLinkError(
                "That share link does not point to a title. "
                "Type the title instead."
            )

        logger.info(
            "[ShareLink] Resolved a share link to '%s' (%s, %s).",
            title,
            year or "year unknown",
            kind or "type unknown",
        )

        return {
            "title": title,
            "year": year,
            "kind": kind,
        }

    async def _follow(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> tuple[str, str]:
        """
        Follow the link's redirects and read the search address.

        Returns the title and Google's identifier, either of which
        may be empty.
        """
        for _ in range(self.MAX_REDIRECTS):
            parts = urlsplit(url)

            if (parts.hostname or "").lower() not in self.ALLOWED_HOSTS:
                break

            query = parse_qs(parts.query)

            # The search address is the destination; it is read, not
            # requested.
            if parts.path == "/search":
                return (
                    (query.get("q") or [""])[0].strip(),
                    (query.get("kgmid") or [""])[0].strip(),
                )

            async with session.get(
                url,
                allow_redirects=False,
            ) as response:
                location = response.headers.get("Location", "")

                if not (300 <= response.status < 400) or not location:
                    break

            url = location

        raise ShareLinkError(
            "That share link does not lead to a Google search result. "
            "Type the title instead."
        )

    async def _lookup(
        self,
        session: aiohttp.ClientSession,
        knowledge_id: str,
    ) -> tuple[int | None, str | None, str | None]:
        """
        Ask Wikidata about Google's identifier.

        Returns the year, the category and the English title. A failed
        lookup is not an error: the title from the link still works,
        only without a year.
        """
        # The identifier is placed inside a query, so it must look
        # exactly like one.
        if not re.fullmatch(r"/[mg]/[0-9a-z_]+", knowledge_id):
            return None, None, None

        # Freebase ids (/m/…) and Google Knowledge Graph ids (/g/…)
        # are separate Wikidata properties.
        prop = "P646" if knowledge_id.startswith("/m/") else "P2671"

        try:
            async with session.get(
                self.WIKIDATA_SPARQL,
                params={
                    "query": self.WIKIDATA_QUERY.format(
                        prop=prop,
                        id=knowledge_id,
                    ),
                },
                headers={
                    "Accept": "application/sparql-results+json",
                },
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"HTTP {response.status}"
                    )

                data = await response.json(
                    content_type=None
                )

            rows = data["results"]["bindings"]

        except Exception as ex:
            logger.warning(
                "[ShareLink] Wikidata lookup failed for %s: %s",
                knowledge_id,
                ex,
            )
            return None, None, None

        if not rows:
            return None, None, None

        types = set()
        years = []

        for row in rows:
            if "type" in row:
                types.add(
                    row["type"]["value"].rsplit("/", 1)[-1]
                )

            # Films have a publication date; series have a start time.
            for key in ("date", "start"):
                match = re.match(
                    r"(\d{4})-",
                    row.get(key, {}).get("value", ""),
                )

                if match:
                    years.append(int(match.group(1)))

        if types & self.SERIES_TYPES:
            kind = "Series"
        elif types & self.FILM_TYPES:
            kind = "Movies"
        else:
            kind = None

        label = rows[0].get("itemLabel", {}).get("value")

        return (
            min(years) if years else None,
            kind,
            label,
        )
