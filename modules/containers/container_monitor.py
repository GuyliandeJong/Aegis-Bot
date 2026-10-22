"""Alerts for Docker containers that crash, turn unhealthy or keep restarting."""

import json
import os
from pathlib import Path

from core.base_monitor import BaseMonitor
from modules.containers.portainer_service import PortainerError, PortainerService
from utils.logger import logger


# Exit codes of a normal stop: clean exit, docker stop (SIGTERM), or a
# stop that ran out of grace (SIGKILL). The backup and /container stop
# end with one of these, so they give no alert.
CALM_EXIT_CODES = {0, 137, 143}


def _problem(container: dict) -> str | None:
    """
    What is wrong with a container, or None.

        crashed     exited with another code than a normal stop
        unhealthy   running, but its healthcheck fails
        restarting  Docker is restarting it (usually a crash loop)
    """
    state = container.get("state")

    if state == "restarting":
        return "restarting"

    if state == "running" and container.get("health") == "unhealthy":
        return "unhealthy"

    if state in ("exited", "dead"):
        code = container.get("exit_code")

        if state == "dead" or (code is not None and code not in CALM_EXIT_CODES):
            return "crashed"

    return None


class ContainerMonitor(BaseMonitor):
    """
    Checks every container through Portainer once a minute.

    Alerts once when a container that was fine crashes (exits with an
    unusual exit code), turns unhealthy, or is being restarted by
    Docker, and once when it is fine again. A container that is already
    unhealthy or restarting at the first check is reported; one that
    had already crashed before is only recorded, so old exits do not
    alert after every bot restart.

    .env:
        CONTAINER_ALERTS          true (default) or false
        CONTAINER_ALERTS_IGNORE   container names to leave alone,
                                  comma separated
    Does nothing without the Portainer settings.
    """

    STATE_FILE = Path("/app/data/container_monitor_state.json")

    INTERVAL = 60
    TIMEOUT = 45

    TITLES = {
        "crashed": "Container Crashed",
        "unhealthy": "Container Unhealthy",
        "restarting": "Container Restarting",
    }

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state: dict = {"containers": {}, "started": False}
        self.load_state()

    @staticmethod
    def enabled() -> bool:
        return os.getenv("CONTAINER_ALERTS", "true").strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def ignored() -> set[str]:
        return {
            name.strip().lower()
            for name in os.getenv("CONTAINER_ALERTS_IGNORE", "").split(",")
            if name.strip()
        }

    def load_state(self):
        try:
            data = json.loads(self.STATE_FILE.read_text())

            if isinstance(data, dict):
                self.state.update(data)
        except FileNotFoundError:
            pass
        except Exception as ex:
            logger.error("[ContainerMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[ContainerMonitor] Failed to save state: %s", ex)

    async def check(self):
        service = PortainerService()

        if not self.enabled() or not service.configured:
            return

        try:
            containers = await service.list_containers(fresh=True)
        except PortainerError as ex:
            # Portainer itself being away is not a container problem.
            logger.warning("[ContainerMonitor] Portainer not reachable: %s", ex)
            return

        known = self.state["containers"]
        first = not self.state.get("started")
        ignored = self.ignored()
        seen = set()

        for container in containers:
            name = container["name"]
            seen.add(name)

            if name.lower() in ignored:
                known.pop(name, None)
                continue

            current = _problem(container)
            previous = known.get(name, {}).get("problem")
            known[name] = {"problem": current}

            if current == previous:
                continue

            if current is None:
                if previous is not None:
                    await self.notification_service.recovered(
                        title=f"Container Recovered — {name}",
                        description=f"**{name}** is running normally again.\n\n`{container['status']}`",
                    )
                continue

            # An exit from before the first check is old news.
            if first and current == "crashed":
                continue

            details = f"`{container['status']}`"

            if current == "crashed":
                details = (
                    f"**{name}** stopped with exit code "
                    f"**{container.get('exit_code')}**, not a normal stop.\n\n{details}"
                )
            elif current == "unhealthy":
                details = f"**{name}** is running, but its healthcheck fails.\n\n{details}"
            else:
                details = f"**{name}** is being restarted by Docker, usually a crash loop.\n\n{details}"

            send = self.notification_service.critical if current == "crashed" else self.notification_service.warning
            await send(
                title=f"{self.TITLES[current]} — {name}",
                description=details + f"\n\nCheck it with `/container logs {name}`.",
            )

        # Removed containers are forgotten.
        for name in list(known):
            if name not in seen:
                known.pop(name)

        self.state["started"] = True
        self.save_state()
