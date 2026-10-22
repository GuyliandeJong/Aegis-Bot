"""Alerts when a power supply or fan reports a problem."""

import json
from pathlib import Path

from core.base_monitor import BaseMonitor
from modules.idrac.hardware_service import HardwareService
from utils.logger import logger


class HardwareMonitor(BaseMonitor):
    """
    Watches the health iDRAC reports for each power supply and fan.

    Health is Redfish's "OK", "Warning" or "Critical". The first check
    only records what is there. Afterwards a change is reported:
    Critical as critical, Warning as a warning, back to OK as
    recovered. A component that was there and is no longer reported
    (pulled, or failed so badly iDRAC lists it as absent) is reported
    as missing.
    """

    STATE_FILE = Path("/app/data/hardware_monitor_state.json")

    def __init__(self, notification_service):
        self.service = HardwareService()
        self.notification_service = notification_service
        self.state: dict[str, str] = {}
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state = {str(k): str(v) for k, v in data.items()}
        except Exception as ex:
            logger.error("[HardwareMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[HardwareMonitor] Failed to save state: %s", ex)

    async def check(self):
        health = await self.service.get_hardware_health()
        components = health["components"]

        # iDRAC answered but listed nothing: do not treat every known
        # component as missing.
        if not components:
            return

        first_run = not self.state
        changed = False

        for name, component in components.items():
            current = component["health"]
            previous = self.state.get(name)

            if previous == current:
                continue

            self.state[name] = current
            changed = True

            if first_run or previous is None:
                continue

            await self._report(name, component, previous, current)

        for name in list(self.state):
            if name in components:
                continue

            previous = self.state.pop(name)
            changed = True

            await self.notification_service.warning(
                title=f"Hardware Missing — {name}",
                description=(
                    f"**{name}** is no longer reported by iDRAC. It "
                    f"was **{previous}** before. Check that it is "
                    "seated and connected."
                ),
            )

        if changed:
            self.save_state()

    async def _report(self, name, component, previous, current):
        kind = component["kind"]
        detail = component.get("detail")
        lines = [
            f"**{kind}**\n{name}",
            f"**Health**\n{previous} → {current}",
        ]

        if detail:
            lines.append(f"**Reading**\n{detail}")

        description = "\n\n".join(lines)

        logger.info(
            "[HardwareMonitor] %s: %s -> %s",
            name,
            previous,
            current,
        )

        if current.lower() == "ok":
            await self.notification_service.recovered(
                title=f"{kind} Recovered — {name}",
                description=description,
            )
        elif current.lower() == "critical":
            await self.notification_service.critical(
                title=f"{kind} Failure — {name}",
                description=description,
            )
        else:
            await self.notification_service.warning(
                title=f"{kind} Warning — {name}",
                description=description,
            )
