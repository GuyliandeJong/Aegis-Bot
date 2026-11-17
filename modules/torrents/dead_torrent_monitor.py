"""Removes torrents that have been dead for days."""

import json
import os
import time

from config.paths import DATA_DIR
from config.settings import settings
from core.base_monitor import BaseMonitor
from modules.torrents.qbittorrent_service import QBittorrentService
from modules.torrents.torrent_requesters import mention_for
from utils.formatting import format_compact_duration
from utils.logger import logger


class DeadTorrentMonitor(BaseMonitor):
    """
    Removes unfinished torrents that make no progress.

    A torrent is dead when it has been downloading metadata or stalled
    (no peers with the missing pieces) and its progress has not grown
    for DEAD_TORRENT_DAYS days. Paused and stopped torrents are never
    dead: the person stopped them. Removing one posts a warning naming
    the torrent and mentions the person who added it, so it can be
    searched again for a better release.

    .env:
        DEAD_TORRENT_DAYS          days without progress before a torrent
                                   is removed (0, the default, turns it
                                   off: removing is not undoable)
        DEAD_TORRENT_DELETE_FILES  true (default) also deletes the partial
                                   download, false keeps the files
    """

    STATE_FILE = DATA_DIR / "dead_torrent_state.json"

    INTERVAL = 600

    # lower case, as qBittorrent's states are compared
    DEAD_STATES = {"stalleddl", "metadl", "forcedmetadl"}

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {}
        self.load_state()

    @staticmethod
    def days() -> float:
        try:
            return max(0.0, float(os.getenv("DEAD_TORRENT_DAYS", "0")))
        except ValueError:
            return 0.0

    @staticmethod
    def delete_files() -> bool:
        value = os.getenv("DEAD_TORRENT_DELETE_FILES", "true")
        return value.strip().lower() not in {"0", "false", "no", "off"}

    @classmethod
    def enabled(cls) -> bool:
        return cls.days() > 0

    @staticmethod
    def configured() -> bool:
        return bool(getattr(settings, "QBITTORRENT_HOST", ""))

    @staticmethod
    def _now() -> float:
        return time.time()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state = data
        except Exception as ex:
            logger.error("[DeadTorrents] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[DeadTorrents] Failed to save state: %s", ex)

    async def check(self):
        if not self.enabled() or not self.configured():
            return

        service = QBittorrentService()

        try:
            torrents = await service.get_torrents_snapshot()
        except Exception as ex:
            logger.warning("[DeadTorrents] Torrent list unavailable: %s", ex)
            return
        finally:
            await service.close()

        if not isinstance(torrents, list):
            return

        now = self._now()
        limit = self.days() * 86400
        seen = set()
        removals = []

        for torrent in torrents:
            key = str(torrent.get("hash") or "").lower()

            if not key:
                continue

            seen.add(key)

            try:
                progress = float(torrent.get("progress") or 0)
            except (TypeError, ValueError):
                progress = 0.0

            dead = (
                progress < 1.0
                and str(torrent.get("state") or "").lower() in self.DEAD_STATES
            )

            entry = self.state.get(key)

            if not dead:
                self.state.pop(key, None)
                continue

            # Progress made since the last look: not dead after all.
            if entry is None or progress > float(entry.get("progress") or 0):
                self.state[key] = {"since": now, "progress": progress}
                continue

            if now - float(entry.get("since") or now) >= limit:
                removals.append((torrent, entry))

        for key in [k for k in self.state if k not in seen]:
            del self.state[key]

        for torrent, entry in removals:
            await self._remove(torrent, entry, now)

        self.save_state()

    async def _remove(self, torrent: dict, entry: dict, now: float) -> None:
        key = str(torrent.get("hash") or "").lower()
        name = str(torrent.get("name") or "Unknown torrent")
        delete_files = self.delete_files()
        service = QBittorrentService()

        try:
            await service.delete_torrent(key, delete_files=delete_files)
        except Exception as ex:
            # Tried again at the next run.
            logger.warning("[DeadTorrents] Could not remove %s: %s", name, ex)
            return
        finally:
            await service.close()

        self.state.pop(key, None)
        waited = format_compact_duration(now - float(entry.get("since") or now))

        await self.notification_service.warning(
            title="Dead Torrent Removed",
            description=(
                f"**{name}** made no progress for {waited} "
                f"(at {float(entry.get('progress') or 0) * 100:.0f}%) and was "
                f"removed{' with its files' if delete_files else ', its files kept'}."
                "\n\nSearch again for a release with more seeders."
            ),
            content=mention_for(key),
        )
