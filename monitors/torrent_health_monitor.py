import json
import time
from pathlib import Path

from config.settings import settings
from services.qbittorrent_service import QBittorrentService
from services.torrent_action_tracker import consume_expected_action
from utils.logger import logger


class TorrentHealthMonitor:
    """Report useful background torrent changes, stalls, and errors."""

    STATE_FILE = Path("/app/data/torrent_health_state.json")
    INTERVAL = 10
    STALL_DURATION = 10 * 60
    BOT_RESUME_SUPPRESSION = 90

    ACTIVE_DOWNLOAD_STATES = {
        "downloading",
        "stalleddl",
        "forceddl",
        "metadl",
    }

    PAUSED_STATES = {
        "pauseddl",
        "pausedup",
        "stoppeddl",
        "stoppedup",
    }

    QUEUED_DOWNLOAD_STATES = {
        "queueddl",
    }

    ERROR_STATES = {
        "error",
        "missingfiles",
    }

    def __init__(self, notification_service):
        self.service = QBittorrentService()
        self.notification_service = notification_service
        self.state = {}
        self.load_state()

    def load_state(self):
        """Load persisted monitoring state."""
        if not self.STATE_FILE.exists():
            return

        try:
            with self.STATE_FILE.open("r") as file:
                data = json.load(file)

            if isinstance(data, dict):
                self.state = data
                torrent_count = sum(
                    1 for key in self.state
                    if key != "_initialized"
                )
                logger.info(
                    "[TorrentHealthMonitor] Loaded state for %d torrents.",
                    torrent_count,
                )
            else:
                logger.warning(
                    "[TorrentHealthMonitor] Invalid state file format."
                )
        except Exception as ex:
            logger.error(
                "[TorrentHealthMonitor] Failed to load state: %s",
                ex,
            )

    def save_state(self):
        """Persist state atomically."""
        temp_file = self.STATE_FILE.with_suffix(".tmp")

        try:
            self.STATE_FILE.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with temp_file.open("w") as file:
                json.dump(self.state, file, indent=4)
                file.flush()

            temp_file.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error(
                "[TorrentHealthMonitor] Failed to save state: %s",
                ex,
            )
            try:
                temp_file.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def number(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def format_duration(seconds):
        seconds = max(0, int(seconds))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")

        return " ".join(parts)

    @staticmethod
    def mention_content():
        user_id = str(
            getattr(settings, "DISCORD_USER_ID", "") or ""
        ).strip()

        return f"<@{user_id}>" if user_id.isdigit() else None

    async def notify_status(
        self,
        title,
        description,
        torrent,
        *,
        recovered=False,
    ):
        notify = (
            self.notification_service.recovered
            if recovered
            else self.notification_service.info
        )

        await notify(
            title=title,
            description=description,
            fields=[
                (
                    "State",
                    f"`{torrent.get('state', 'unknown')}`",
                    True,
                ),
                (
                    "Category",
                    str(torrent.get("category") or "Uncategorized"),
                    True,
                ),
            ],
            content=self.mention_content(),
        )

    async def notify_stalled(self, torrent, stalled_for):
        name = str(torrent.get("name") or "Unknown torrent")

        await self.notification_service.warning(
            title="⚠️ Torrent Stalled",
            description=(
                f"**{name}** has had zero download speed for "
                f"{self.format_duration(stalled_for)}."
            ),
            fields=[
                (
                    "State",
                    f"`{torrent.get('state', 'unknown')}`",
                    True,
                ),
                (
                    "Category",
                    str(torrent.get("category") or "Uncategorized"),
                    True,
                ),
            ],
            content=self.mention_content(),
        )

        logger.warning(
            "[TorrentHealthMonitor] Torrent stalled: %s",
            name,
        )

    async def notify_error(self, torrent):
        name = str(torrent.get("name") or "Unknown torrent")

        await self.notification_service.warning(
            title="⚠️ Torrent Error",
            description=f"**{name}** is in a qBittorrent error state.",
            fields=[
                (
                    "State",
                    f"`{torrent.get('state', 'unknown')}`",
                    True,
                ),
                (
                    "Category",
                    str(torrent.get("category") or "Uncategorized"),
                    True,
                ),
            ],
            content=self.mention_content(),
        )

        logger.warning(
            "[TorrentHealthMonitor] Torrent error state: %s",
            name,
        )

    async def check(self):
        """Check current torrents and report meaningful changes."""
        torrents = await self.service.get_torrents()

        if not isinstance(torrents, list):
            raise RuntimeError(
                "qBittorrent returned an invalid torrent list."
            )

        now = time.time()
        previous_state = self.state

        was_initialized = (
            bool(previous_state.get("_initialized"))
            or any(key != "_initialized" for key in previous_state)
        )

        current_state = {"_initialized": True}

        for torrent in torrents:
            torrent_hash = torrent.get("hash")
            if not torrent_hash:
                logger.warning(
                    "[TorrentHealthMonitor] Torrent without hash received."
                )
                continue

            torrent_hash = str(torrent_hash).lower()
            name = str(torrent.get("name") or "Unknown torrent")
            status = str(torrent.get("state") or "unknown").lower()
            download_speed = self.number(torrent.get("dlspeed", 0))
            progress = self.number(torrent.get("progress", 0))

            previous = previous_state.get(torrent_hash)
            entry = dict(previous or {})
            old_status = str(entry.get("last_status") or "").lower()

            entry["name"] = name
            entry["category"] = str(torrent.get("category") or "")

            expected_action = None
            if old_status and old_status != status:
                expected_action = consume_expected_action(
                    torrent_hash,
                    status,
                )

            # Report state changes only after a baseline scan and only when
            # the bot itself did not just issue the matching action.
            if old_status and status in self.ACTIVE_DOWNLOAD_STATES:
                if old_status in self.PAUSED_STATES:
                    if expected_action != "resume":
                        await self.notify_status(
                            "▶️ Torrent Resumed",
                            f"**{name}** is downloading again.",
                            torrent,
                        )

                elif old_status in self.QUEUED_DOWNLOAD_STATES:
                    suppress_until = self.number(
                        entry.get("bot_resume_suppress_until", 0)
                    )

                    if now <= suppress_until:
                        entry["bot_resume_suppress_until"] = 0
                    else:
                        await self.notify_status(
                            "▶️ Torrent Started",
                            f"**{name}** has started downloading from the queue.",
                            torrent,
                        )

            elif (
                old_status in self.ACTIVE_DOWNLOAD_STATES
                and status in self.PAUSED_STATES
                and expected_action != "pause"
                and not (status == "pausedup" and progress >= 1.0)
            ):
                await self.notify_status(
                    "⚠️ Torrent Stopped",
                    f"**{name}** stopped downloading.",
                    torrent,
                )

            if (
                expected_action == "resume"
                and status in self.QUEUED_DOWNLOAD_STATES
            ):
                entry["bot_resume_suppress_until"] = (
                    now + self.BOT_RESUME_SUPPRESSION
                )

            if status in self.ERROR_STATES:
                entry["stalled_since"] = None
                entry["stalled_notified"] = False

                if not entry.get("error_notified", False):
                    await self.notify_error(torrent)
                    entry["error_notified"] = True

                entry["last_status"] = status
                current_state[torrent_hash] = entry
                continue

            if entry.get("error_notified", False):
                await self.notify_status(
                    "✅ Torrent Recovered",
                    f"**{name}** recovered from its error state.",
                    torrent,
                    recovered=True,
                )
                entry["error_notified"] = False

            stall_was_notified = entry.get(
                "stalled_notified",
                False,
            )

            if (
                status in self.ACTIVE_DOWNLOAD_STATES
                and download_speed <= 0
            ):
                stalled_since = entry.get("stalled_since")

                if stalled_since is None:
                    stalled_since = now
                    entry["stalled_since"] = stalled_since

                stalled_for = max(0, now - float(stalled_since))

                if (
                    stalled_for >= self.STALL_DURATION
                    and not stall_was_notified
                ):
                    await self.notify_stalled(torrent, stalled_for)
                    entry["stalled_notified"] = True

            else:
                if (
                    stall_was_notified
                    and status in self.ACTIVE_DOWNLOAD_STATES
                    and download_speed > 0
                ):
                    await self.notify_status(
                        "✅ Torrent Recovered",
                        f"**{name}** resumed downloading after being stalled.",
                        torrent,
                        recovered=True,
                    )

                entry["stalled_since"] = None
                entry["stalled_notified"] = False

            entry["last_status"] = status
            current_state[torrent_hash] = entry

        self.state = current_state

        if self.state != previous_state:
            self.save_state()