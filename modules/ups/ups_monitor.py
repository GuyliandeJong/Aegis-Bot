"""Alerts for power outages, a low battery and a battery that needs replacing."""

import json
import time

from config.paths import DATA_DIR
from core import activity_log
from core.base_monitor import BaseMonitor
from modules.ups.nut_service import NutError, NutService
from utils.formatting import format_compact_duration
from utils.logger import logger


class UpsMonitor(BaseMonitor):
    """
    Reads the UPS status from a NUT server every 30 seconds.

    - On battery ("OB"): a warning "Power Outage" with the charge and
      the runtime left, once; "Power Restored" with how long it lasted
      when the UPS is on line power ("OL") again.
    - Low battery ("LB"): a critical "UPS Battery Low", once per outage.
    - Replace battery ("RB"): a warning, once until it is cleared.
    - The NUT server not answering for three checks in a row: a warning,
      and "UPS Monitor Reachable Again".

    The bot only sees an outage while its own host is still powered, so
    the server it runs on should be on the UPS.

    .env:
        NUT_HOST, NUT_PORT (default 3493), NUT_UPS (default "ups")
    """

    STATE_FILE = DATA_DIR / "ups_monitor_state.json"

    INTERVAL = 30
    TIMEOUT = 30

    FAILURES_BEFORE_ALERT = 3

    VARIABLES = ["ups.status", "battery.charge", "battery.runtime", "ups.load"]

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {
            "on_battery_since": None,
            "low": False,
            "replace": False,
            "failures": 0,
            "down": False,
        }
        self.load_state()

    @staticmethod
    def _now() -> float:
        return time.time()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[UpsMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[UpsMonitor] Failed to save state: %s", ex)

    async def check(self):
        service = NutService()

        if not service.configured:
            return

        try:
            values = await service.read(self.VARIABLES)
        except NutError as ex:
            await self._unreachable(str(ex))
            self.save_state()
            return

        if self.state["down"]:
            await self.notification_service.recovered(
                title="UPS Monitor Reachable Again",
                description=f"The NUT server answers again for `{service.ups}`.",
            )

        self.state["failures"] = 0
        self.state["down"] = False

        status = values.get("ups.status", "").split()

        if status:
            await self._status(status, values)

        self.save_state()

    async def _unreachable(self, reason: str) -> None:
        self.state["failures"] += 1

        if self.state["failures"] >= self.FAILURES_BEFORE_ALERT and not self.state["down"]:
            self.state["down"] = True
            await self.notification_service.warning(
                title="UPS Monitor Not Reachable",
                description=(
                    f"{reason}\n\nPower outages are not seen until the NUT "
                    "server answers again."
                ),
            )

    @staticmethod
    def _battery(values: dict) -> str:
        parts = []

        if values.get("battery.charge"):
            parts.append(f"Battery {values['battery.charge']}%")

        try:
            runtime = int(float(values.get("battery.runtime", "")))
            parts.append(f"about {format_compact_duration(runtime)} left")
        except ValueError:
            pass

        if values.get("ups.load"):
            parts.append(f"load {values['ups.load']}%")

        return ", ".join(parts) or "No battery details reported."

    async def _status(self, status: list[str], values: dict) -> None:
        now = self._now()
        battery = self._battery(values)

        if "OB" in status:
            if self.state["on_battery_since"] is None:
                self.state["on_battery_since"] = now
                await self.notification_service.warning(
                    title="Power Outage",
                    description=f"The UPS is running on battery.\n\n{battery}",
                )

            if "LB" in status and not self.state["low"]:
                self.state["low"] = True
                await self.notification_service.critical(
                    title="UPS Battery Low",
                    description=f"The UPS battery is nearly empty.\n\n{battery}",
                )
        elif "OL" in status and self.state["on_battery_since"] is not None:
            seconds = max(0, now - self.state["on_battery_since"])
            self.state["on_battery_since"] = None
            self.state["low"] = False
            activity_log.record("power_outage", seconds=int(seconds))
            await self.notification_service.recovered(
                title="Power Restored",
                description=(
                    f"The UPS is on line power again after "
                    f"{format_compact_duration(seconds)}.\n\n{battery}"
                ),
            )

        replace = "RB" in status

        if replace and not self.state["replace"]:
            self.state["replace"] = True
            await self.notification_service.warning(
                title="UPS Battery Needs Replacing",
                description=f"The UPS reports that its battery should be replaced.\n\n{battery}",
            )
        elif not replace:
            self.state["replace"] = False
