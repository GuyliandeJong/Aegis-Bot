"""Alerts when someone's playback makes Jellyfin transcode."""

import json
import os
import time

import discord

from config.paths import DATA_DIR
from core.base_monitor import BaseMonitor
from modules.jellyfin.jellyfin_service import JellyfinService
from modules.jellyfin.transcode import describe_reasons
from utils.formatting import format_compact_duration
from utils.logger import logger


class TranscodeMonitor(BaseMonitor):
    """
    Watches the streams on the primary Jellyfin server and posts one
    warning per stream that is being transcoded.

    Transcoding costs CPU or GPU, and usually means the player cannot
    handle the file (codec, container, subtitles, bitrate limit); the
    alert names the person, the device and Jellyfin's reasons.

    - Video transcoding is reported. Audio-only transcoding (the video
      is passed through) is cheap and only reported with
      JELLYFIN_TRANSCODE_AUDIO_ALERTS=true. A server that does not say
      whether the video is passed through counts as video transcoding.
    - A stream must be transcoded for CHECKS_BEFORE_ALERT checks in a
      row, so Jellyfin switching to direct play a moment after the start
      does not alert.
    - One alert per stream. After CLEAR_AFTER checks without
      transcoding, "Jellyfin Transcoding Stopped" is posted (it says
      whether the stream is still playing or ended, and how long the
      transcoding lasted) and the stream is forgotten, so a later
      transcode alerts again. A stream that was never alerted is
      forgotten quietly. A server that does not answer changes nothing.

    Only the primary server (JELLYFIN_1_*) is watched; the others are
    usually someone else's.

    .env:
        JELLYFIN_TRANSCODE_ALERTS        true (default) or false
        JELLYFIN_TRANSCODE_AUDIO_ALERTS  true or false (default)
    """

    STATE_FILE = DATA_DIR / "transcode_monitor_state.json"

    INTERVAL = 30
    TIMEOUT = 30

    CHECKS_BEFORE_ALERT = 2
    CLEAR_AFTER = 2

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"streams": {}}
        self.load_state()

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _on(name: str, default: str) -> bool:
        return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}

    @classmethod
    def enabled(cls) -> bool:
        return cls._on("JELLYFIN_TRANSCODE_ALERTS", "true")

    @classmethod
    def audio_alerts(cls) -> bool:
        return cls._on("JELLYFIN_TRANSCODE_AUDIO_ALERTS", "false")

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict) and isinstance(data.get("streams"), dict):
                    self.state = {"streams": data["streams"]}
        except Exception as ex:
            logger.error("[TranscodeMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[TranscodeMonitor] Failed to save state: %s", ex)

    @staticmethod
    def stream_key(session: dict) -> str:
        return "|".join(
            str(session.get(field) or "")
            for field in ("user", "device", "client", "title")
        )

    @classmethod
    def is_reported(cls, session: dict) -> bool:
        """Whether a stream is transcoding in a way that is reported."""
        if session.get("method") != "Transcode":
            return False

        if session.get("video_direct") is True:
            return cls.audio_alerts()

        return True

    async def check(self):
        if not self.enabled():
            return

        service = JellyfinService()

        try:
            primary = service.primary_server()

            if primary is None:
                return

            sessions = await service.now_playing(primary.name)
        except Exception as ex:
            logger.warning("[TranscodeMonitor] Jellyfin not reachable: %s", ex)
            return
        finally:
            await service.close()

        # Not answering is not "stopped transcoding".
        if sessions is None:
            return

        streams = self.state["streams"]
        playing = {self.stream_key(s) for s in sessions}
        current = {
            self.stream_key(s): s for s in sessions if self.is_reported(s)
        }

        for key in list(streams):
            if key not in current:
                streams[key]["missed"] = streams[key].get("missed", 0) + 1

                if streams[key]["missed"] >= self.CLEAR_AFTER:
                    entry = streams.pop(key)
                    self.save_state()

                    if entry.get("alerted"):
                        await self._stopped(key, entry, key in playing, primary.name)

        for key, session in current.items():
            entry = streams.setdefault(key, {"seen": 0, "alerted": False})
            entry["seen"] += 1
            entry["missed"] = 0

            if entry["seen"] >= self.CHECKS_BEFORE_ALERT and not entry["alerted"]:
                entry["alerted"] = True
                entry["since"] = self._now()
                self.save_state()
                await self._alert(session, primary.name)

        self.save_state()

    async def _alert(self, session: dict, server: str) -> None:
        escape = discord.utils.escape_markdown
        where = session.get("device") or session.get("client") or "an unknown device"
        what = "video" if session.get("video_direct") is not True else "audio"
        text = f"Transcoding the {what}"

        if what == "video" and session.get("height"):
            text += f" to {session['height']}p"

        reasons = describe_reasons(session.get("reasons"))

        if reasons:
            text += f" because of: {', '.join(reasons)}"

        await self.notification_service.warning(
            title="Jellyfin Is Transcoding",
            description=(
                f"**{escape(session.get('title') or 'Unknown')}** for "
                f"{escape(session.get('user') or 'an unknown user')} on "
                f"{escape(where)} ({escape(server)}).\n\n{text}.\n\n"
                "Direct play avoids the load: a player that handles the "
                "file's codec, container and subtitles does not need it."
            ),
        )

    async def _stopped(self, key: str, entry: dict, still_playing: bool, server: str) -> None:
        user, device, client, title = (key.split("|", 3) + ["", "", "", ""])[:4]
        escape = discord.utils.escape_markdown
        where = device or client or "an unknown device"
        since = entry.get("since")
        lasted = (
            f" after {format_compact_duration(max(0, self._now() - since))}"
            if isinstance(since, (int, float))
            else ""
        )
        text = (
            "The video is no longer being transcoded."
            if still_playing
            else "Playback ended."
        )

        await self.notification_service.recovered(
            title="Jellyfin Transcoding Stopped",
            description=(
                f"**{escape(title or 'Unknown')}** for "
                f"{escape(user or 'an unknown user')} on "
                f"{escape(where)} ({escape(server)}){lasted}.\n\n{text}"
            ),
        )
