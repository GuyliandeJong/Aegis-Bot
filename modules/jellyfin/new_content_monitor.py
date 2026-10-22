"""
Once a day, report seasons and episodes another Jellyfin server has
of series the primary library already holds.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import discord

from config.settings import settings
from core.base_monitor import BaseMonitor
from modules.jellyfin.jellyfin_compare import (
    describe_missing,
    match_series,
    missing_content,
    missing_keys,
)
from modules.jellyfin.jellyfin_service import JellyfinService
from utils.logger import logger


class JellyfinNewContentMonitor(BaseMonitor):
    """
    Compares every series in the primary library with the other
    Jellyfin servers once a day, and posts what is new.

    Each server is asked twice per run: once for all series, once for
    all episodes. Only content not reported before is posted, so the
    same missing season is announced once, not every day.

    .env:
        JELLYFIN_NEW_CONTENT_ALERTS  true (default) or false
        JELLYFIN_NEW_CONTENT_HOUR    hour of the day to run, 0-23
                                     (default 10, container time)
    """

    STATE_FILE = Path("/app/data/jellyfin_new_content_state.json")

    # Checks every ten minutes whether today's run is due.
    INTERVAL = 600

    # Listing whole libraries can take a few minutes.
    TIMEOUT = 900

    # More lines than this in one alert are summarized.
    MAX_LINES = 20

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"last_run": None, "reported": {}}
        self.load_state()

    @staticmethod
    def enabled() -> bool:
        value = os.getenv("JELLYFIN_NEW_CONTENT_ALERTS", "true")
        return value.strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def run_hour() -> int:
        try:
            return min(23, max(0, int(os.getenv("JELLYFIN_NEW_CONTENT_HOUR", "10"))))
        except ValueError:
            return 10

    def load_state(self):
        if not self.STATE_FILE.exists():
            return

        try:
            with self.STATE_FILE.open("r") as file:
                data = json.load(file)

            if isinstance(data, dict):
                self.state["last_run"] = data.get("last_run")
                reported = data.get("reported")

                if isinstance(reported, dict):
                    self.state["reported"] = reported

        except Exception as ex:
            logger.error("[NewContent] Failed to load state: %s", ex)

    def save_state(self):
        temporary = self.STATE_FILE.with_suffix(".tmp")

        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

            with temporary.open("w") as file:
                json.dump(self.state, file, indent=4)

            temporary.replace(self.STATE_FILE)

        except Exception as ex:
            logger.error("[NewContent] Failed to save state: %s", ex)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    async def check(self):
        if not self.enabled():
            return

        service = JellyfinService()

        if len(service.servers) < 2 or service.primary_server() is None:
            return

        now = self._now()
        today = now.strftime("%Y-%m-%d")

        if self.state.get("last_run") == today or now.hour < self.run_hour():
            return

        try:
            findings = await self.scan(service)
        finally:
            await service.close()

        self.state["last_run"] = today
        self.save_state()

        if findings:
            await self.post(findings)

    async def scan(self, service: JellyfinService) -> list[dict]:
        """Compare the libraries and return what was not reported yet."""
        primary = service.primary_server()

        own_series = await service.list_library(primary.name, "Series")

        if not own_series:
            logger.warning(
                "[NewContent] %s returned no series; skipping today's "
                "comparison.",
                primary.name,
            )
            return []

        own_episodes = self._by_series(
            await service.list_library(primary.name, "Episode")
        )

        previous = self.state.get("reported") or {}
        reported = {}
        findings = []

        for server in service.servers:
            if server.primary:
                continue

            their_series = await service.list_library(server.name, "Series")

            if not their_series:
                # The server did not answer. Keep what was reported
                # for it, so nothing is announced twice later.
                logger.warning(
                    "[NewContent] %s returned no series.",
                    server.name,
                )
                reported.update(
                    {
                        key: value
                        for key, value in previous.items()
                        if key.startswith(f"{server.name}|")
                    }
                )
                continue

            their_episodes = self._by_series(
                await service.list_library(server.name, "Episode")
            )

            pairs = match_series(own_series, their_series)

            logger.info(
                "[NewContent] %d of %d series also on %s.",
                len(pairs),
                len(own_series),
                server.name,
            )

            for own, theirs in pairs:
                own_list = own_episodes.get(str(own.get("Id")), [])

                # A series without any episodes in the primary library
                # is probably still being scanned; offering all of it
                # again would be wrong.
                if not own_list:
                    continue

                missing = missing_content(
                    own_list,
                    their_episodes.get(str(theirs.get("Id")), []),
                )

                if not missing:
                    continue

                key = f"{server.name}|{theirs.get('Id')}"
                keys = missing_keys(missing)
                reported[key] = keys

                if set(keys) - set(previous.get(key, [])):
                    findings.append(
                        {
                            "server": server.name,
                            "own": own,
                            "theirs": theirs,
                            "label": self._label(own),
                            "summary": describe_missing(missing, limit=3),
                        }
                    )

        self.state["reported"] = reported

        logger.info(
            "[NewContent] %d series with new content.",
            len(findings),
        )

        return findings

    @staticmethod
    def _by_series(episodes: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}

        for episode in episodes:
            series_id = str(episode.get("SeriesId") or "")

            if series_id:
                grouped.setdefault(series_id, []).append(episode)

        return grouped

    @staticmethod
    def _label(item: dict) -> str:
        name = str(item.get("Name") or "Unknown title")
        year = item.get("ProductionYear")
        return f"{name} ({year})" if year else name

    async def post(self, findings: list[dict]) -> None:
        bot = self.notification_service.bot
        channel = bot.get_channel(settings.ALERT_CHANNEL_ID)

        if channel is None:
            logger.error("[NewContent] Alert channel not found.")
            return

        servers = sorted({finding["server"] for finding in findings})

        lines = [
            f"**{finding['label']}** on {finding['server']}: "
            f"{finding['summary']}"
            for finding in findings[: self.MAX_LINES]
        ]

        if len(findings) > self.MAX_LINES:
            lines.append(f"…and {len(findings) - self.MAX_LINES} more.")

        can_download = JellyfinService.library_dir("Series") is not None

        if can_download:
            lines.append("")
            lines.append(
                "Pick a series below to copy what is missing."
                if len(findings) <= 25
                else "The first 25 can be picked below; search the "
                "others with `/torrent search`."
            )

        embed = discord.Embed(
            title=f"New episodes on {', '.join(servers)}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text="Aegis-Bot")

        view = None

        if can_download:
            # Imported here: monitors load before the command modules.
            from modules.jellyfin.new_content_view import NewContentView

            view = NewContentView(bot, findings)

        message = await channel.send(embed=embed, view=view)

        if view is not None:
            view.message = message
