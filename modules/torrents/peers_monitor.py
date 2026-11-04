"""Alerts, and optionally restarts qBittorrent, when it has lost every peer."""

import asyncio
import json
import math
import os
import time

from config.paths import DATA_DIR
from config.settings import settings
from core import activity_log
from core.base_monitor import BaseMonitor
from modules.containers.portainer_service import PortainerError, PortainerService
from modules.torrents.qbittorrent_service import QBittorrentService
from utils.logger import logger


_tasks: set[asyncio.Task] = set()


class NoPeersMonitor(BaseMonitor):
    """
    Notices a qBittorrent that stays connected to nobody.

    After a network drop (a switch restarting, the VPN reconnecting)
    qBittorrent can stay in a dead state: it reports zero DHT nodes
    and torrents sit in "downloading metadata" until it is restarted.
    Nothing else notices, because the WebUI still answers.

    The signal is `dht_nodes` in /transfer/info being 0 for
    QBITTORRENT_NO_PEERS_MINUTES in a row, while DHT is enabled in
    qBittorrent's preferences (checked only when the count is 0). A
    server that is unreachable, or answers without these fields,
    neither adds to nor resets the count. "firewalled" is not a
    problem: it is normal behind a VPN.

    When the time is up a warning is posted once. If
    QBITTORRENT_NO_PEERS_RESTART names a container, it is restarted
    through Portainer, like /container restart (so PORTAINER_PROTECTED
    applies). Loop guards: no counting for GRACE_SECONDS after a
    restart (DHT needs minutes to bootstrap); at most MAX_RESTARTS
    restarts in RESTART_WINDOW seconds; then one "Restart Did Not Help"
    warning and no more restarts until peers are back. "Peers Again" is
    posted when the node count goes above 0.

    WAN_RECOVERY_RESTART restarts after an outage within about a
    minute; this monitor waits much longer, so it only acts when that
    did not help or was not set up.

    .env:
        QBITTORRENT_NO_PEERS_MINUTES  minutes without DHT nodes before
                                      acting (default 10, 0 turns it off)
        QBITTORRENT_NO_PEERS_RESTART  container to restart, as named in
                                      Portainer (empty: only alert)
    """

    STATE_FILE = DATA_DIR / "no_peers_monitor_state.json"

    INTERVAL = 120
    TIMEOUT = 60

    GRACE_SECONDS = 600
    MAX_RESTARTS = 2
    RESTART_WINDOW = 6 * 3600

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {
            "zero": 0,
            "alerted": False,
            "gave_up": False,
            "restarts": [],
            "grace_until": 0,
        }

        self.load_state()

    @staticmethod
    def minutes() -> int:
        try:
            return max(0, int(os.getenv("QBITTORRENT_NO_PEERS_MINUTES", "10")))
        except ValueError:
            return 10

    @staticmethod
    def container() -> str:
        return os.getenv("QBITTORRENT_NO_PEERS_RESTART", "").strip()

    @staticmethod
    def enabled() -> bool:
        return NoPeersMonitor.minutes() > 0

    @staticmethod
    def configured() -> bool:
        return bool(getattr(settings, "QBITTORRENT_HOST", ""))

    @staticmethod
    def _now() -> float:
        return time.time()

    def checks_needed(self) -> int:
        return max(1, math.ceil(self.minutes() * 60 / self.INTERVAL))

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[NoPeersMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[NoPeersMonitor] Failed to save state: %s", ex)

    @staticmethod
    async def dht_enabled(service) -> bool | None:
        """True or False from the preferences; None when unknown."""
        try:
            preferences = await service.get_preferences()
        except Exception as ex:
            logger.warning("[NoPeersMonitor] Preferences not readable: %s", ex)
            return None

        value = preferences.get("dht") if isinstance(preferences, dict) else None
        return value if isinstance(value, bool) else None

    async def check(self):
        if not self.enabled() or not self.configured():
            return

        service = QBittorrentService()

        try:
            transfer = await service.get_transfer_info()

            nodes = transfer.get("dht_nodes") if isinstance(transfer, dict) else None
            dht = None

            if isinstance(nodes, int) and not isinstance(nodes, bool) and nodes == 0:
                dht = await self.dht_enabled(service)
        except Exception as ex:
            # Down or restarting: not counted either way.
            logger.warning("[NoPeersMonitor] qBittorrent not reachable: %s", ex)
            return
        finally:
            await service.close()

        if not isinstance(nodes, int) or isinstance(nodes, bool):
            return

        if nodes > 0:
            await self._peers(nodes)
        elif dht is True:
            await self._no_peers()
        elif dht is False:
            self.state["zero"] = 0
        else:
            return

        self.save_state()

    async def _peers(self, nodes: int) -> None:
        was_down = self.state["alerted"] or self.state["gave_up"]

        self.state["zero"] = 0
        self.state["alerted"] = False
        self.state["gave_up"] = False

        if was_down:
            await self.notification_service.recovered(
                title="qBittorrent Has Peers Again",
                description=f"qBittorrent is connected to {nodes} DHT nodes again.",
            )

    async def _no_peers(self) -> None:
        now = self._now()

        if now < self.state["grace_until"]:
            return

        self.state["zero"] += 1

        if self.state["zero"] < self.checks_needed():
            return

        window = [t for t in self.state["restarts"] if now - t < self.RESTART_WINDOW]
        self.state["restarts"] = window
        name = self.container()
        problem = self._restart_problem(name)

        if name and not problem and len(window) < self.MAX_RESTARTS:
            first = not self.state["alerted"]
            self.state["alerted"] = True
            self.state["zero"] = 0
            self.state["restarts"].append(now)
            self.state["grace_until"] = now + self.GRACE_SECONDS

            if first:
                await self.notification_service.warning(
                    title="qBittorrent Has No Peers",
                    description=self._text() + f"\n\nRestarting `{name}`.",
                )

            self._start_restart(name)
            return

        if name and not problem and not self.state["gave_up"]:
            self.state["gave_up"] = True
            self.state["alerted"] = True
            await self.notification_service.warning(
                title="qBittorrent Restart Did Not Help",
                description=(
                    f"`{name}` was restarted {len(window)} times and "
                    "qBittorrent still has no DHT nodes. No more restarts "
                    "until peers are back. Check the VPN connection."
                ),
            )
            return

        if not self.state["alerted"]:
            self.state["alerted"] = True
            extra = f"\n\nNo automatic restart: {problem}." if problem else (
                "\n\nRestart qBittorrent's container to fix it."
            )
            await self.notification_service.warning(
                title="qBittorrent Has No Peers",
                description=self._text() + extra,
            )

    def _text(self) -> str:
        return (
            "qBittorrent has had no DHT nodes for "
            f"{self.minutes()} minutes, so downloads cannot find peers. "
            "A network drop is the usual cause."
        )

    @staticmethod
    def _restart_problem(name: str) -> str | None:
        if name and not PortainerService().configured:
            return "Portainer is not configured"

        return None

    def _start_restart(self, name: str) -> None:
        task = asyncio.create_task(self._restart(name))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    async def _restart(self, name: str) -> None:
        try:
            service = PortainerService()
            container = await service.find_container(name)
            await service.perform("restart", container)
            activity_log.record("no_peers_restart", container=name, ok=True)
            logger.info("[NoPeersMonitor] Restarted %s.", name)
            await self.notification_service.success(
                title="qBittorrent Restarted",
                description=(
                    f"Restarted `{name}` because qBittorrent had no peers. "
                    "Peers can take a few minutes to return."
                ),
            )
        except PortainerError as ex:
            await self._failed(name, str(ex))
        except Exception as ex:
            logger.exception("[NoPeersMonitor] Restarting %s failed: %s", name, ex)
            await self._failed(name, "unexpected error, see the bot log")

    async def _failed(self, name: str, reason: str) -> None:
        activity_log.record("no_peers_restart", container=name, ok=False)
        logger.warning("[NoPeersMonitor] Could not restart %s: %s", name, reason)

        try:
            await self.notification_service.warning(
                title="qBittorrent Restart Failed",
                description=(
                    f"`{name}`: {reason}\n\nRestart it by hand with "
                    "`/container restart`."
                ),
            )
        except Exception:
            logger.exception("[NoPeersMonitor] Could not post the result.")
