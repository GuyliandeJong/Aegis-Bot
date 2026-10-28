"""Twice a day, look again for the titles on people's watchlists."""

import asyncio
import json
import time

import discord

from config.paths import DATA_DIR
from config.settings import settings
from core.base_monitor import BaseMonitor
from modules.torrents import watchlist
from modules.jellyfin.jellyfin_service import JellyfinService
from utils.logger import logger


class WatchlistMonitor(BaseMonitor):
    """
    Searches Jellyfin and Jackett for every watchlist entry.

    Jellyfin is asked first: a title that appeared on any configured
    server counts as found. Otherwise Jackett is searched exactly as
    `/torrent search` does, including the broadened fallback search
    and its relevance filter. A found entry is announced to its owner
    and removed from the watchlist.
    """

    STATE_FILE = DATA_DIR / "watchlist_monitor_state.json"

    INTERVAL = 600
    TIMEOUT = 1800

    # Twice a day.
    EVERY = 12 * 60 * 60

    # Pause between entries, so the indexers are not hit in a burst.
    PAUSE = 5

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.last_run = 0.0
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())
                self.last_run = float(data.get("last_run") or 0)
        except Exception as ex:
            logger.error("[Watchlist] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps({"last_run": self.last_run}))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[Watchlist] Failed to save state: %s", ex)

    @staticmethod
    def _now() -> float:
        return time.time()

    def _searcher(self):
        """The `/torrent` cog, whose search logic is reused."""
        return self.notification_service.bot.get_cog("Torrent")

    async def check(self):
        entries = watchlist.all_entries()

        if not entries:
            return

        if self._now() - self.last_run < self.EVERY:
            return

        searcher = self._searcher()

        if searcher is None:
            return

        self.last_run = self._now()
        self.save_state()

        logger.info("[Watchlist] Checking %d title(s).", len(entries))

        for index, entry in enumerate(entries):
            if index:
                await asyncio.sleep(self.PAUSE)

            try:
                found = await self.look_for(searcher, entry)
            except Exception as ex:
                logger.warning(
                    "[Watchlist] Checking '%s' failed: %s",
                    entry["query"],
                    ex,
                )
                continue

            if found:
                await self.announce(entry, found)
                watchlist.remove(entry["id"])

    async def look_for(self, searcher, entry: dict) -> str | None:
        """Return a description of where the title was found, or None."""
        if entry["category"] in {"Movies", "Series"}:
            service = JellyfinService()

            try:
                matches = await service.find_matches(
                    entry["query"],
                    entry["category"],
                )
            finally:
                await service.close()

            if matches:
                servers = ", ".join(str(m.get("server")) for m in matches)
                return f"It is on Jellyfin now: **{servers}**."

        results, _errors, _searched, _broadened = (
            await searcher._find_torrents(
                entry["query"],
                entry["category"],
            )
        )

        # Releases the person already saw and passed on do not count.
        seen = set(entry.get("seen") or [])
        results = [
            result
            for result in results
            if watchlist.result_key(result) not in seen
        ]

        if results:
            new = "new " if seen else ""

            return (
                f"{len(results)} {new}torrent result"
                f"{'s' if len(results) != 1 else ''} found."
            )

        return None

    async def announce(self, entry: dict, found: str) -> None:
        bot = self.notification_service.bot

        channel = None

        if str(entry.get("channel_id") or "").isdigit():
            channel = bot.get_channel(int(entry["channel_id"]))

        if channel is None:
            channel = bot.get_channel(settings.ALERT_CHANNEL_ID)

        if channel is None:
            logger.error("[Watchlist] No channel to announce in.")
            return

        # Imported here: monitors load before the command modules.
        from modules.torrents.torrent.watchlist import WatchlistFoundView

        view = WatchlistFoundView(bot, entry)

        logger.info(
            "[Watchlist] Found '%s' for user %s.",
            entry["query"],
            entry["user_id"],
        )

        view.message = await channel.send(
            content=f"<@{entry['user_id']}>",
            embed=discord.Embed(
                title=f"Found: {entry['query']}",
                description=(
                    f"{found}\n\nIt was on your watchlist "
                    f"({entry['category']}) and has been removed from "
                    "it. Press **Show results** to search now."
                ),
                color=discord.Color.green(),
            ),
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
