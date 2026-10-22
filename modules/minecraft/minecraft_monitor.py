"""Players joining and leaving Minecraft servers, and watched servers going down."""

import json
import os

from config.paths import DATA_DIR
from core.base_monitor import BaseMonitor
from modules.minecraft import minecraft_service
from utils.logger import logger


class MinecraftMonitor(BaseMonitor):
    """
    Asks every server in MINECRAFT_SERVERS for its status each minute.

    Joins and leaves: "X joined <server>" and "X left <server>" as info,
    only when MINECRAFT_JOIN_ALERTS is true (default false). They are
    only compared when the server listed every player online; servers
    with more than 12 players or hidden names give no join alerts.

    Down: a server named in MINECRAFT_WATCH (comma separated, default
    empty) that does not answer two checks in a row gives a warning,
    and a recovery when it answers again. Other servers are stopped on
    purpose now and then and give no alerts.

    Does nothing when MINECRAFT_SERVERS is empty.
    """

    STATE_FILE = DATA_DIR / "minecraft_monitor_state.json"

    INTERVAL = 60
    TIMEOUT = 30

    # Failed checks in a row before a watched server counts as down.
    FAILURES_BEFORE_ALERT = 2

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"servers": {}}
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[MinecraftMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[MinecraftMonitor] Failed to save state: %s", ex)

    @staticmethod
    def join_alerts() -> bool:
        value = os.getenv("MINECRAFT_JOIN_ALERTS", "false")
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def watched() -> set[str]:
        return {
            name.strip().lower()
            for name in os.getenv("MINECRAFT_WATCH", "").split(",")
            if name.strip()
        }

    async def check(self):
        if not minecraft_service.configured():
            return

        results = await minecraft_service.fetch_all()
        known = self.state["servers"]
        watched = self.watched()

        # Forget servers that were removed from MINECRAFT_SERVERS.
        for name in list(known):
            if name not in {r["name"] for r in results}:
                del known[name]

        for result in results:
            name = result["name"]
            entry = known.setdefault(
                name, {"players": None, "failures": 0, "down": False}
            )

            if result["up"]:
                await self._answering(name, entry, result)
            else:
                await self._not_answering(name, entry, result, name.lower() in watched)

        self.save_state()

    async def _not_answering(self, name: str, entry: dict, result: dict, watched: bool) -> None:
        entry["failures"] += 1

        # Players are not known while it is down; whoever is online when
        # it comes back is recorded without join alerts.
        entry["players"] = None

        if (
            watched
            and entry["failures"] >= self.FAILURES_BEFORE_ALERT
            and not entry["down"]
        ):
            entry["down"] = True
            await self.notification_service.warning(
                title=f"Minecraft Server Down — {name}",
                description=(
                    f"**{name}** ({result['host']}:{result['port']}) has not "
                    f"answered {entry['failures']} checks in a row "
                    f"(every {self.INTERVAL} seconds).\n\n"
                    f"`{result['error'][:200]}`"
                ),
            )

    async def _answering(self, name: str, entry: dict, result: dict) -> None:
        if entry["down"]:
            await self.notification_service.recovered(
                title=f"Minecraft Server Recovered — {name}",
                description=(
                    f"**{name}** answers again: {result['version']}, "
                    f"{result['online']}/{result['max']} players."
                ),
            )

        entry["failures"] = 0
        entry["down"] = False

        previous = entry["players"]
        current = result["players"] if result["complete"] else None
        entry["players"] = current

        if previous is None or current is None or not self.join_alerts():
            return

        joined = [p for p in current if p not in previous]
        left = [p for p in previous if p not in current]
        online = ", ".join(current) or "nobody"

        if joined:
            await self.notification_service.info(
                title=f"{self._who(joined)} joined {name}",
                description=f"**Online now** ({result['online']}/{result['max']})\n{online}",
            )

        if left:
            await self.notification_service.info(
                title=f"{self._who(left)} left {name}",
                description=f"**Online now** ({result['online']}/{result['max']})\n{online}",
            )

    @staticmethod
    def _who(names: list[str]) -> str:
        # An embed title holds 256 characters.
        text = ", ".join(names)
        return text if len(text) <= 150 else f"{len(names)} players"
