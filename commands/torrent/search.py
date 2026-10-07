"""Jackett-backed torrent search command implementation."""

import json
import math
import re
from pathlib import Path

import discord

from commands.torrent.jellyfin_download import (
    JellyfinSourceView,
    existing_series_folder,
    expire_view,
    find_missing_content,
)
from commands.torrent.watchlist import WatchlistAddView
from services.jackett_service import JackettService
from services.jellyfin_compare import (
    better_copy,
    describe_missing,
    describe_quality,
)
from services.jellyfin_service import JellyfinService
from services.share_link_service import (
    ShareLinkError,
    ShareLinkService,
)
from utils.formatting import format_bytes
from utils.logger import logger
from utils.open_messages import ExpiringView


class TorrentSearchSelect(discord.ui.Select):
    """Dropdown used to choose a Jackett search result."""

    def __init__(self, search_view):
        self.search_view = search_view

        options = []

        for index, result in enumerate(
            search_view.results,
            start=1,
        ):
            label = search_view.torrent_cog._truncate_name(
                f"{index}. {result['title']}",
                95,
            )

            description = (
                f"{result['indexer']} · "
                f"score {result['score']} · "
                f"{result['seeders']} seeders"
            )

            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(index - 1),
                    description=description[:100],
                )
            )

        super().__init__(
            placeholder="Choose a torrent to add…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        await self.search_view.choose_result(
            interaction,
            int(self.values[0]),
        )


class TorrentSearchCancelButton(discord.ui.Button):
    """Cancel the current torrent search."""

    def __init__(self, search_view):
        self.search_view = search_view

        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        await self.search_view.cancel(
            interaction
        )


class TorrentJellyfinSearchButton(discord.ui.Button):
    """Continue with the normal Jackett search after a Jellyfin hit."""
    def __init__(self, search_view):
        self.search_view = search_view
        super().__init__(
            label="Search for torrent",
            style=discord.ButtonStyle.primary,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        self.search_view.stop()

        # Deferring a button press shows nothing in Discord, and a
        # Jackett search can take a long time. Replace the Jellyfin
        # message straight away so the user can see the search started.
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(
                title="Searching for torrents…",
                description=(
                    f"Searching Jackett for "
                    f"**{self.search_view.query}**.\n"
                    "This can take a little while."
                ),
                color=discord.Color.blurple(),
            ),
            view=None,
        )

        await self.search_view.torrent_cog._search(
            interaction,
            query=self.search_view.query,
            category=self.search_view.category,
            skip_jellyfin=True,
        )


class TorrentJellyfinSearchView(ExpiringView):
    """Owner-restricted view shown when Jellyfin already has the title."""
    def __init__(
        self,
        owner_id,
        query,
        category,
        torrent_cog,
    ):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.query = query
        self.category = category
        self.torrent_cog = torrent_cog
        self.message = None
        self.add_item(
            TorrentJellyfinSearchButton(self)
        )

    async def on_timeout(self):
        await expire_view(self)

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who started this search can use this button.",
            ephemeral=True,
        )
        return False


class TorrentSearchView(ExpiringView):
    """Owner-restricted torrent-search result picker."""

    def __init__(
        self,
        owner_id,
        category,
        results,
        torrent_cog,
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.category = category
        self.results = results
        self.torrent_cog = torrent_cog
        self.message = None

        self.add_item(
            TorrentSearchSelect(self)
        )

        self.add_item(
            TorrentSearchCancelButton(self)
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who started this search can "
            "control it.",
            ephemeral=True,
        )

        return False

    async def choose_result(
        self,
        interaction,
        result_index,
    ):
        """Resolve and add the selected torrent."""
        result = self.results[result_index]

        logger.info(
            "[TorrentSearch] %s selected '%s' from %s.",
            interaction.user,
            result["title"],
            result["indexer"],
        )

        # Resolving a Jackett download URL can take far longer than the
        # three seconds Discord allows before an interaction expires.
        await interaction.response.defer()

        service = JackettService()

        try:
            magnet = str(
                result.get("magnet") or ""
            ).strip()

            # Normal magnet results need no extra request.
            #
            # Indexers such as 1337x can return an HTTP Jackett
            # download URL. Resolve that URL only now, after
            # the user has actually selected the result.
            if not magnet.lower().startswith("magnet:?"):
                result = await service.resolve_result(
                    result
                )

                magnet = str(
                    result.get("magnet") or ""
                ).strip()

        except Exception as ex:
            logger.exception(
                "[TorrentSearch] Failed to resolve "
                "selected result: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to resolve the selected torrent. "
                "Please try another result.",
                ephemeral=True,
            )
            return

        finally:
            await service.close()

        if not magnet.lower().startswith("magnet:?"):
            await interaction.followup.send(
                "The selected release could not be resolved "
                "to a usable magnet link. Please try another result.",
                ephemeral=True,
            )
            return

        # TorrentAddMixin owns validation, qBittorrent submission,
        # the live information embed, and its control buttons.
        await self.torrent_cog._add(
            interaction,
            category=self.category,
            magnet=magnet,
        )

    async def cancel(
        self,
        interaction,
    ):
        """Cancel the search and remove its controls."""
        logger.info(
            "[TorrentSearch] %s cancelled a torrent search.",
            interaction.user,
        )

        self.stop()

        await interaction.response.edit_message(
            content="Torrent search cancelled.",
            embed=None,
            view=None,
        )

    async def on_timeout(self):
        """Disable the picker once the interaction expires."""
        for child in self.children:
            child.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(
                    view=self
                )
            except discord.HTTPException:
                pass


class TorrentSearchMixin:
    """Mixin providing scored Jackett searches."""

    PREFERENCES_FILE = Path(
        "/app/config/torrent_preferences.json"
    )

    MAX_RESULTS = 25
    DISPLAY_RESULTS = 10

    REJECTED_TERMS = {
        "cam",
        "hdcam",
        "hdts",
        "telesync",
        "telecine",
        "tc",
        "ts",
    }

    SEARCH_STOP_WORDS = {
        "a",
        "an",
        "and",
        "in",
        "of",
        "on",
        "the",
        "to",
        "for",
        "with",
    }

    RESOLUTION_PATTERNS = (
        ("2160p", r"\b(?:2160p|4k|uhd)\b"),
        ("1080p", r"\b1080p\b"),
        ("720p", r"\b720p\b"),
    )

    @staticmethod
    def _search_tokens(value):
        """Return normalized words suitable for title matching."""
        return {
            token
            for token in re.findall(
                r"[a-z0-9]+",
                str(value).lower(),
            )
            if len(token) >= 2
        }

    @staticmethod
    def _title_has_term(title_lower, term):
        """
        Check whether a preference term appears in a title as a whole
        word or phrase.

        A plain substring test is not enough: "iso" is also found
        inside "episode".
        """
        term = str(term).strip().lower()

        if not term:
            return False

        return re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            title_lower,
        ) is not None

    @classmethod
    def _query_tokens(cls, value):
        """Return ordered normalized query tokens."""
        return re.findall(
            r"[a-z0-9]+",
            str(value).lower(),
        )

    @classmethod
    def _meaningful_query_tokens(cls, value):
        """Return query tokens excluding common stop words."""
        return [
            token
            for token in cls._query_tokens(value)
            if token not in cls.SEARCH_STOP_WORDS
        ]

    @classmethod
    def _token_matches_title(
        cls,
        query_token,
        title_tokens,
    ):
        """
        Check whether a query token meaningfully matches a title token.

        Exact matches are preferred, but incomplete words can also
        match the beginning of a title word.

        Example:

            bo -> boots
        """
        if query_token in title_tokens:
            return True

        if len(query_token) < 2:
            return False

        return any(
            title_token.startswith(query_token)
            for title_token in title_tokens
        )

    @classmethod
    def _fallback_relevance(
        cls,
        result,
        original_query,
    ):
        """
        Determine how strongly a fallback result matches the
        original user query.

        Returns a value from 0.0 to 1.0.

        Example:

            Puss in bo
            Puss in Boots The Last Wish

        matches:

            puss -> puss
            bo   -> boots

        while:

            Puss in bo
            Puss N Bo

        does not sufficiently match because "in" is missing.
        """
        original_tokens = (
            cls._meaningful_query_tokens(
                original_query
            )
        )

        if not original_tokens:
            return 0.0

        title_tokens = cls._search_tokens(
            result.get("title")
        )

        if not title_tokens:
            return 0.0

        matched = sum(
            1
            for token in original_tokens
            if cls._token_matches_title(
                token,
                title_tokens,
            )
        )

        return matched / len(original_tokens)

    @classmethod
    def _filter_fallback_results(
        cls,
        results,
        original_query,
    ):
        """
        Remove fallback results that are not sufficiently relevant
        to the original user query.

        For multi-word queries we require strong coverage of the
        meaningful query terms.
        """
        if not results:
            return []

        original_tokens = (
            cls._meaningful_query_tokens(
                original_query
            )
        )

        if not original_tokens:
            return []

        minimum_relevance = 1.0

        if len(original_tokens) >= 4:
            minimum_relevance = 0.75

        filtered = []

        for result in results:
            relevance = cls._fallback_relevance(
                result,
                original_query,
            )

            result["_fallback_relevance"] = relevance

            if relevance >= minimum_relevance:
                filtered.append(result)

        return filtered

    @classmethod
    def _search_fallback_queries(cls, query):
        """
        Build conservative fallback queries.

        The original query is handled separately.

        Examples:

            Puss in bo
                -> Puss in

            The Last of Us par
                -> The Last of Us

            Lord of the Rings
                -> Lord of the Rings
                -> Lord Rings
        """
        tokens = cls._query_tokens(query)

        if len(tokens) < 2:
            return []

        candidates = []

        def add_candidate(candidate_tokens):
            if not candidate_tokens:
                return

            candidate = " ".join(
                candidate_tokens
            ).strip()

            if (
                candidate
                and candidate != query.lower()
                and candidate not in candidates
            ):
                candidates.append(candidate)

        # Most conservative fallback:
        #
        # Remove only the final token while preserving everything
        # before it.
        #
        # "Puss in bo" -> "Puss in"
        add_candidate(
            tokens[:-1]
        )

        # Next fallback:
        #
        # Remove common stop words while preserving word order.
        meaningful_tokens = [
            token
            for token in tokens[:-1]
            if token not in cls.SEARCH_STOP_WORDS
        ]

        if meaningful_tokens:
            add_candidate(
                meaningful_tokens
            )

        # Absolute last resort:
        #
        # Search for the first meaningful word.
        #
        # Results are still filtered against the complete original
        # query, so unrelated releases should be rejected.
        if len(meaningful_tokens) >= 2:
            add_candidate(
                meaningful_tokens[:1]
            )

        return candidates

    def _load_preferences(self):
        """Load editable torrent scoring preferences."""
        if not self.PREFERENCES_FILE.exists():
            logger.warning(
                "[TorrentSearch] Preference file is missing: %s",
                self.PREFERENCES_FILE,
            )
            return {}

        try:
            with self.PREFERENCES_FILE.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

            if isinstance(data, dict):
                return data

            logger.warning(
                "[TorrentSearch] Preference file must contain "
                "a JSON object."
            )

        except Exception as ex:
            logger.error(
                "[TorrentSearch] Failed to load preferences: %s",
                ex,
            )

        return {}

    @classmethod
    def _result_resolution(cls, title):
        """Return the best-known resolution found in a title."""
        title = str(title).lower()

        for resolution, pattern in cls.RESOLUTION_PATTERNS:
            if re.search(
                pattern,
                title,
            ):
                return resolution

        return None

    @classmethod
    def _has_preferred_resolution(
        cls,
        results,
        preferred_resolutions,
    ):
        """Check whether a stronger preferred result exists."""
        preferred = {
            str(value).lower()
            for value in preferred_resolutions
        }

        if not preferred:
            return False

        return any(
            cls._result_resolution(
                result.get("title")
            ) in preferred
            for result in results
        )

    @staticmethod
    def _category_preferences(
        preferences,
        category,
    ):
        """Return the rules for the selected qBittorrent category."""
        categories = preferences.get(
            "categories",
            {},
        )

        if not isinstance(
            categories,
            dict,
        ):
            return {}

        result = categories.get(
            category,
            {},
        )

        return (
            result
            if isinstance(result, dict)
            else {}
        )

    @classmethod
    def _score_result(
        cls,
        result,
        query,
        category_preferences,
        has_preferred_resolution,
    ):
        """Score title relevance, swarm health, and preferences."""
        title = str(
            result.get("title") or ""
        )

        title_lower = title.lower()

        query_tokens = cls._search_tokens(
            query
        )

        title_tokens = cls._search_tokens(
            title
        )

        score = 0
        reasons = []

        matched_tokens = len(
            query_tokens & title_tokens
        )

        if query_tokens:
            coverage = (
                matched_tokens
                / len(query_tokens)
            )

            score += round(
                coverage * 50
            )

            if coverage == 1:
                reasons.append(
                    "title match"
                )
            elif coverage >= 0.6:
                reasons.append(
                    "close title match"
                )
            else:
                reasons.append(
                    "partial title match"
                )

        normalized_query = re.sub(
            r"[^a-z0-9]+",
            "",
            str(query).lower(),
        )

        normalized_title = re.sub(
            r"[^a-z0-9]+",
            "",
            title_lower,
        )

        if (
            normalized_query
            and normalized_query
            in normalized_title
        ):
            score += 20

        seeders = max(
            0,
            int(
                result.get("seeders")
                or 0
            ),
        )

        leechers = max(
            0,
            int(
                result.get("leechers")
                or 0
            ),
        )

        if seeders:
            score += min(
                30,
                round(
                    math.log10(
                        seeders + 1
                    ) * 12
                ),
            )

            reasons.append(
                f"{seeders} seeders"
            )

        if (
            seeders > leechers
            and seeders >= 5
        ):
            score += 5
            reasons.append(
                "healthy swarm"
            )

        rejected = (
            cls.REJECTED_TERMS
            & cls._search_tokens(title)
        )

        if rejected:
            score -= 75
            reasons.append(
                "low-quality source"
            )

        resolution = cls._result_resolution(
            title
        )

        resolution_scores = (
            category_preferences.get(
                "resolution_scores",
                {},
            )
        )

        fallback_scores = (
            category_preferences.get(
                "fallback_resolution_scores",
                {},
            )
        )

        if resolution:
            resolution_score = int(
                resolution_scores.get(
                    resolution,
                    0,
                )
            )

            if (
                resolution in fallback_scores
                and not has_preferred_resolution
            ):
                resolution_score = int(
                    fallback_scores[
                        resolution
                    ]
                )

                reasons.append(
                    f"{resolution} fallback"
                )

            elif resolution_score:
                if resolution_score > 0:
                    reasons.append(
                        f"{resolution} preferred"
                    )
                else:
                    reasons.append(
                        f"{resolution} lower priority"
                    )

            score += resolution_score

        codec_scores = (
            category_preferences.get(
                "codec_scores",
                {},
            )
        )

        matched_codec_scores = [
            int(codec_score)
            for codec, codec_score
            in codec_scores.items()
            if cls._title_has_term(
                title_lower,
                codec,
            )
        ]

        if matched_codec_scores:
            codec_score = max(
                matched_codec_scores
            )

            score += codec_score

            if codec_score > 0:
                reasons.append(
                    "efficient encode"
                )

        avoid_term_scores = (
            category_preferences.get(
                "avoid_term_scores",
                {},
            )
        )

        matched_avoid_scores = [
            int(term_score)
            for term, term_score
            in avoid_term_scores.items()
            if cls._title_has_term(
                title_lower,
                term,
            )
        ]

        if matched_avoid_scores:
            avoid_score = min(
                matched_avoid_scores
            )

            score += avoid_score

            reasons.append(
                "large source release"
            )

        size = max(
            0,
            int(
                result.get("size")
                or 0
            ),
        )

        if size == 0:
            score -= 5

        result["score"] = score

        result["score_reason"] = (
            ", ".join(
                reasons[:3]
            )
            or "basic match"
        )

        return result

    @staticmethod
    def _source_name(indexer):
        """Turn a Jackett indexer ID into a readable label."""
        names = {
            "rutracker-ru": "RuTracker",
            "thepiratebay": "The Pirate Bay",
            "1337x": "1337x",
        }

        return names.get(
            indexer,
            str(indexer).replace(
                "-",
                " ",
            ).title(),
        )

    def _build_search_embed(
        self,
        query,
        category,
        results,
        errors,
    ):
        """Build a Discord-safe search-result embed."""
        embed = discord.Embed(
            title="Torrent Search",
            description=(
                f"Results for **{query}**\n"
                f"Target category: **{category}**\n\n"
                "Choose a release from the dropdown below. "
                "Results are ordered by recommendation score."
            ),
            color=discord.Color.blurple(),
        )

        for position, result in enumerate(
            results[
                :self.DISPLAY_RESULTS
            ],
            start=1,
        ):
            title = self._truncate_name(
                result["title"],
                210,
            )

            source = self._source_name(
                result["indexer"]
            )

            embed.add_field(
                name=f"{position}. {title}",
                value=(
                    f"`Score {result['score']}` · "
                    f"{source}\n"
                    f"🌱 {result['seeders']} seeders · "
                    f"📦 {self._format_bytes(result['size'])}\n"
                    f"_{result['score_reason']}_"
                ),
                inline=False,
            )

        source_names = sorted(
            {
                self._source_name(
                    result["indexer"]
                )
                for result in results
            }
        )

        embed.set_footer(
            text=(
                f"{len(results)} ranked result(s) · "
                f"Sources: {', '.join(source_names)}"
            )
        )

        if errors:
            unavailable = ", ".join(
                self._source_name(indexer)
                for indexer in errors
            )

            embed.add_field(
                name="Unavailable source",
                value=unavailable,
                inline=False,
            )

        return embed

    async def _run_search(
        self,
        service,
        query,
    ):
        """Run one Jackett search."""
        return await service.search(
            query
        )

    async def _check_jellyfin(
        self,
        query,
        category,
    ):
        """
        Check configured Jellyfin servers before torrent searching.
        A Jellyfin failure is non-fatal and never prevents Jackett
        from being searched.
        """
        if category not in {"Movies", "Series"}:
            return []

        service = JellyfinService()

        if not service.servers:
            await service.close()
            return []

        try:
            return await service.find_matches(
                query,
                category,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentSearch] Jellyfin availability check failed: %s",
                ex,
            )
            return []

        finally:
            await service.close()

    @staticmethod
    def _jellyfin_download_source(matches, category):
        """
        Return the match a title can be downloaded from, if any.

        A download is only offered when the primary library does not
        have the title, another server does, and a library folder is
        configured for the category.
        """
        if any(match.get("primary") for match in matches):
            return None

        if JellyfinService.library_dir(category) is None:
            return None

        for match in matches:
            if match.get("item_id"):
                return match

        return None

    @staticmethod
    def _build_jellyfin_embed(
        matches,
        download_source=None,
        missing=None,
        better=None,
    ):
        """
        Build the "already available" embed.

        Each Jellyfin server that has the title is listed by name, so
        the user can see whose library it is in, with the quality of
        its copy for movies.
        """
        lines = []

        for match in matches:
            title = str(
                match.get("title")
                or "Unknown title"
            )

            year = match.get("year")

            if year:
                title = f"{title} ({year})"

            server_url = str(
                match.get("url")
                or ""
            ).strip()

            line = f"**{match.get('server')}** — {title}"

            if server_url:
                line += f"\n`{server_url}`"

            quality = describe_quality(
                match.get("quality"),
                format_bytes,
            )

            if quality and match.get("type") == "Movie":
                line += f"\n{quality}"

            subtitles = JellyfinService.describe_subtitles(
                match.get("subtitles")
            )

            if subtitles:
                line += f"\n{subtitles}"

            lines.append(line)

        if len(matches) == 1:
            title = (
                f"Already available on {matches[0].get('server')}"
            )
        else:
            title = (
                f"Already available on {len(matches)} Jellyfin servers"
            )

        if missing:
            server = download_source.get("server")

            if all(entry["complete"] for entry in missing):
                title = f"{server} has more seasons"
            else:
                title = f"{server} has more episodes"

            closing = (
                f"Your library is missing: "
                f"**{describe_missing(missing)}**.\n"
                f"You can copy them from {server} or search for "
                "a torrent instead."
            )

        elif download_source is not None:
            title = f"Available on {download_source.get('server')}"
            closing = (
                "It is not in your own library yet. You can copy it "
                f"from {download_source.get('server')} or search for "
                "a torrent instead."
            )
        else:
            closing = "No torrent search has been performed."

        if better is not None:
            closing = (
                f"**{better.get('server')}** has a copy that fits your "
                "torrent preferences better. Replacing your copy is "
                "up to you; the bot does not touch library files.\n\n"
                + closing
            )

        return discord.Embed(
            title=title,
            description=(
                "\n\n".join(lines)
                + f"\n\n{closing}"
            ),
            color=discord.Color.green(),
        )

    async def _find_torrents(
        self,
        query,
        category,
    ):
        """
        Search Jackett and rank the results by the preferences.

        Tries the exact query first and broadens it only when that
        finds nothing. Returns (ranked results, errors per indexer,
        the query that was searched, whether it was broadened).
        Raises when Jackett cannot be searched at all.
        """
        service = JackettService()

        try:
            # Always try the exact user query first.
            results, errors = (
                await self._run_search(
                    service,
                    query,
                )
            )

            searched_query = query
            fallback_used = False

            # Only broaden the search if the exact query returned
            # absolutely nothing.
            if not results:
                fallback_queries = (
                    self._search_fallback_queries(
                        query
                    )
                )

                for fallback_query in fallback_queries:
                    logger.info(
                        "[TorrentSearch] No results for '%s'. "
                        "Trying fallback query '%s'.",
                        query,
                        fallback_query,
                    )

                    (
                        fallback_results,
                        fallback_errors,
                    ) = await self._run_search(
                        service,
                        fallback_query,
                    )

                    errors.update(
                        fallback_errors
                    )

                    if not fallback_results:
                        continue

                    # A fallback query may return results that are
                    # technically valid to Jackett but unrelated to
                    # what the user typed. Filter them against the
                    # original query before accepting them.
                    relevant_results = (
                        self._filter_fallback_results(
                            fallback_results,
                            query,
                        )
                    )

                    if not relevant_results:
                        logger.info(
                            "[TorrentSearch] Fallback query "
                            "'%s' returned results, but none "
                            "were sufficiently relevant to '%s'.",
                            fallback_query,
                            query,
                        )
                        continue

                    results = relevant_results
                    searched_query = fallback_query
                    fallback_used = True

                    logger.info(
                        "[TorrentSearch] Fallback query "
                        "'%s' returned %d relevant result(s).",
                        fallback_query,
                        len(results),
                    )

                    break

        finally:
            await service.close()

        preferences = self._load_preferences()

        category_preferences = (
            self._category_preferences(
                preferences,
                category,
            )
        )

        has_preferred_resolution = (
            self._has_preferred_resolution(
                results,
                category_preferences.get(
                    "preferred_resolutions",
                    [],
                ),
            )
        )

        ranked_results = [
            self._score_result(
                result,
                query,
                category_preferences,
                has_preferred_resolution,
            )
            for result in results
        ]

        ranked_results.sort(
            key=lambda result: (
                result["score"],
                result["seeders"],
                result["grabs"],
            ),
            reverse=True,
        )

        ranked_results = ranked_results[
            :self.MAX_RESULTS
        ]

        return (
            ranked_results,
            errors,
            searched_query,
            fallback_used,
        )

    async def _search(
        self,
        interaction,
        query,
        category,
        skip_jellyfin=False,
    ):
        """Search Jellyfin first, then Jackett if necessary."""
        query = str(
            query or ""
        ).strip()

        if not query:
            await interaction.response.send_message(
                "Please provide something to search for.",
                ephemeral=True,
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        # A Google share link can be pasted instead of a title. It is
        # turned into "Title Year", and tells movies from series.
        if ShareLinkService.is_link(query):
            try:
                resolved = await ShareLinkService().resolve(query)

            except ShareLinkError as ex:
                await interaction.edit_original_response(
                    content=str(ex),
                    embed=None,
                    view=None,
                )
                return

            query = resolved["title"]

            if (
                resolved["kind"]
                and category in {"Movies", "Series"}
                and resolved["kind"] != category
            ):
                logger.info(
                    "[TorrentSearch] '%s' is in %s, not %s; "
                    "searching that category instead.",
                    query,
                    resolved["kind"],
                    category,
                )

                category = resolved["kind"]

            # Movie releases carry their year, so adding it narrows
            # the search. Series releases are named by season instead,
            # and a year would hide most of them.
            if (
                category == "Movies"
                and resolved["year"]
                and str(resolved["year"]) not in query
            ):
                query = f"{query} {resolved['year']}"

        # Check Jellyfin before creating or querying Jackett.
        #
        # Only Movies and Series are checked. Music and PC continue
        # directly to the normal Jackett search.
        jellyfin_matches = []
        if not skip_jellyfin:
            jellyfin_matches = await self._check_jellyfin(
                query,
                category,
            )

        if jellyfin_matches:
            logger.info(
                "[TorrentSearch] '%s' is already available on %s. "
                "Skipping torrent search.",
                query,
                ", ".join(
                    str(match.get("server"))
                    for match in jellyfin_matches
                ),
            )

            download_source = self._jellyfin_download_source(
                jellyfin_matches,
                category,
            )

            # The primary library may have the series but lack seasons
            # or episodes that another server has.
            missing = None
            own_episodes = None
            folder_name = None

            if download_source is None:
                try:
                    found = await find_missing_content(
                        jellyfin_matches,
                        category,
                    )
                except Exception as ex:
                    logger.exception(
                        "[TorrentSearch] Episode comparison failed: %s",
                        ex,
                    )
                    found = None

                if found is not None:
                    download_source = found["source"]
                    missing = found["missing"]
                    own_episodes = found["own_episodes"]

                    folder_name = existing_series_folder(
                        found["primary"],
                        JellyfinService.library_dir(category),
                    )

            # Flag a better copy on another server, for movies; series
            # episodes differ too much from file to file to compare.
            better = None

            if category == "Movies":
                better = better_copy(
                    jellyfin_matches,
                    self._category_preferences(
                        self._load_preferences(),
                        category,
                    ),
                )

            if download_source is not None:
                view = JellyfinSourceView(
                    owner_id=interaction.user.id,
                    query=query,
                    category=category,
                    torrent_cog=self,
                    match=download_source,
                    missing=missing,
                    own_episodes=own_episodes,
                    folder_name=folder_name,
                )
            else:
                view = TorrentJellyfinSearchView(
                    owner_id=interaction.user.id,
                    query=query,
                    category=category,
                    torrent_cog=self,
                )

            # Kept so the view can grey out its buttons when it expires.
            view.message = await interaction.edit_original_response(
                content=None,
                embed=self._build_jellyfin_embed(
                    jellyfin_matches,
                    download_source,
                    missing,
                    better,
                ),
                view=view,
            )
            return

        try:
            (
                ranked_results,
                errors,
                searched_query,
                fallback_used,
            ) = await self._find_torrents(
                query,
                category,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentSearch] Jackett search failed: %s",
                ex,
            )

            await interaction.edit_original_response(
                content=(
                    "Unable to search Jackett. Check the bot logs "
                    "and the Jackett connection settings."
                ),
                embed=None,
                view=None,
            )
            return

        if not ranked_results:
            detail = ""

            if errors:
                detail = (
                    "\nJackett could not query: "
                    + ", ".join(
                        self._source_name(
                            indexer
                        )
                        for indexer in errors
                    )
                )

            view = WatchlistAddView(
                interaction.user.id,
                query,
                category,
            )

            view.message = await interaction.edit_original_response(
                content=(
                    f"No magnet-capable results found for "
                    f"**{query}**.{detail}\n"
                    "Press **Watch for it** to be told when it turns up."
                ),
                embed=None,
                view=view,
            )
            return

        embed = self._build_search_embed(
            query,
            category,
            ranked_results,
            errors,
        )

        if fallback_used:
            embed.description = (
                f"Results for **{query}**\n"
                f"Target category: **{category}**\n\n"
                f"Search broadened to **{searched_query}** "
                "because the exact search returned no results.\n\n"
                "Choose a release from the dropdown below. "
                "Results are ordered by recommendation score."
            )

        view = TorrentSearchView(
            owner_id=interaction.user.id,
            category=category,
            results=ranked_results,
            torrent_cog=self,
        )

        try:
            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=view,
            )
        except discord.HTTPException as ex:
            logger.exception(
                "[TorrentSearch] Failed to display results: %s",
                ex,
            )
            raise

        view.message = (
            await interaction.original_response()
        )

        logger.info(
            "[TorrentSearch] Returned %d result(s) for '%s' "
            "using query '%s', requested by %s.",
            len(ranked_results),
            query,
            searched_query,
            interaction.user,
        )
