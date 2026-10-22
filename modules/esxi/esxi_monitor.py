"""Alerts for ESXi datastores filling up, virtual machines stopping and old snapshots."""

import json
import os
import time

from config.paths import DATA_DIR
from core.base_monitor import BaseMonitor
from modules.esxi import esxi_service
from utils.formatting import format_bytes
from utils.logger import logger


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class EsxiMonitor(BaseMonitor):
    """
    Checks the ESXi host every five minutes.

    Datastores: warning at ESXI_DATASTORE_WARNING percent used
    (default 85), critical at ESXI_DATASTORE_CRITICAL (default 95) or
    when the datastore is not accessible; recovered below the warning
    level. Changes that are not critical respect ALERT_COOLDOWN.

    Virtual machines: an alert when one that was running stops or is
    suspended, and when it runs again. Names in ESXI_IGNORE_VMS
    (comma separated) are not watched.

    Snapshots: a warning, at most once a day per VM, while a VM has a
    snapshot older than ESXI_SNAPSHOT_DAYS (default 7; 0 turns this
    off), and "Snapshot Removed" once when there is none any more.

    The first check records the current state; of that, only a
    datastore that is already filling up or inaccessible is reported.
    Does nothing when ESXI_HOST, ESXI_USERNAME or ESXI_PASSWORD is
    missing.
    """

    STATE_FILE = DATA_DIR / "esxi_monitor_state.json"

    INTERVAL = 300
    TIMEOUT = 120

    # Failed checks in a row before "not reachable" is reported.
    FAILURES_BEFORE_ALERT = 3

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {
            "datastores": {}, "vms": {}, "snapshots": {}, "alerts": {},
            "failures": 0, "down": False,
        }
        self.load_state()

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[EsxiMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[EsxiMonitor] Failed to save state: %s", ex)

    @staticmethod
    def ignored_vms() -> set[str]:
        return {
            name.strip().lower()
            for name in os.getenv("ESXI_IGNORE_VMS", "").split(",")
            if name.strip()
        }

    @staticmethod
    def datastore_level(datastore: dict) -> str:
        if not datastore["accessible"]:
            return "critical"

        if not datastore["capacity"]:
            return "healthy"

        used = 100 - datastore["free"] / datastore["capacity"] * 100

        if used >= _number("ESXI_DATASTORE_CRITICAL", 95):
            return "critical"

        if used >= _number("ESXI_DATASTORE_WARNING", 85):
            return "warning"

        return "healthy"

    @staticmethod
    def snapshot_days() -> float:
        return max(_number("ESXI_SNAPSHOT_DAYS", 7), 0)

    @staticmethod
    def age(created: float | None) -> str:
        """Age of a snapshot in whole days, for alerts and /esxi."""
        if created is None:
            return "unknown age"

        days = int((time.time() - created) // 86400)

        if days < 1:
            return "less than a day"

        return f"{days} day{'s' if days != 1 else ''}"

    def _cooldown_passed(self, key: str) -> bool:
        last = self.state["alerts"].get(key, 0)
        return time.time() - last >= self.alert_cooldown()

    async def check(self):
        if not esxi_service.configured():
            return

        try:
            status = await esxi_service.fetch_status()
        except Exception as ex:
            self.state["failures"] += 1
            logger.warning("[EsxiMonitor] ESXi not reachable: %s", ex)

            if (
                self.state["failures"] >= self.FAILURES_BEFORE_ALERT
                and not self.state["down"]
            ):
                self.state["down"] = True
                await self.notification_service.warning(
                    title="ESXi Not Reachable",
                    description=(
                        f"The ESXi host has not answered "
                        f"{self.state['failures']} checks in a row "
                        f"(every {self.INTERVAL // 60} minutes).\n\n"
                        f"`{str(ex)[:200]}`"
                    ),
                )

            self.save_state()
            return

        if self.state["down"]:
            await self.notification_service.recovered(
                title="ESXi Reachable Again",
                description="The ESXi host answers again.",
            )

        self.state["failures"] = 0
        self.state["down"] = False

        await self._datastores(status["datastores"])
        await self._vms(status["vms"])
        await self._snapshots(status["vms"])

        self.save_state()

    async def _datastores(self, datastores: list[dict]) -> None:
        known = self.state["datastores"]

        for datastore in datastores:
            name = datastore["name"]
            current = self.datastore_level(datastore)
            previous = known.get(name)

            if previous == current:
                continue

            key = f"datastore:{name}"

            if (
                previous is not None
                and current != "critical"
                and not self._cooldown_passed(key)
            ):
                continue

            known[name] = current

            # A datastore seen for the first time is only reported when
            # it is already filling up; a healthy one is just recorded.
            if previous is None and current == "healthy":
                continue

            capacity = datastore["capacity"]
            free = datastore["free"]
            used = 100 - free / capacity * 100 if capacity else 0
            description = (
                f"**Datastore**\n{name}\n\n"
                f"**Used**\n{used:.0f}% ({format_bytes(capacity - free)} "
                f"of {format_bytes(capacity)})\n\n"
                f"**Free**\n{format_bytes(free)}"
            )

            if not datastore["accessible"]:
                description = f"**Datastore**\n{name}\n\nThe datastore is not accessible."

            self.state["alerts"][key] = time.time()

            if current == "critical":
                await self.notification_service.critical(
                    title=f"Datastore Critical — {name}",
                    description=description,
                )
            elif current == "warning":
                await self.notification_service.warning(
                    title=f"Datastore Filling Up — {name}",
                    description=description,
                )
            else:
                await self.notification_service.recovered(
                    title=f"Datastore Recovered — {name}",
                    description=description,
                )

    async def _vms(self, vms: list[dict]) -> None:
        known = self.state["vms"]
        ignored = self.ignored_vms()

        for vm in vms:
            name = vm["name"]

            if name.lower() in ignored:
                continue

            current = vm["power"]
            previous = known.get(name)
            known[name] = current

            if previous is None or previous == current:
                continue

            if previous == "poweredOn":
                await self.notification_service.warning(
                    title=f"VM Stopped — {name}",
                    description=(
                        f"**{name}** went from running to "
                        f"**{self._power(current)}**."
                    ),
                )
            elif current == "poweredOn":
                await self.notification_service.recovered(
                    title=f"VM Running — {name}",
                    description=f"**{name}** is running again.",
                )

    async def _snapshots(self, vms: list[dict]) -> None:
        # Per VM name: the time of the last "Old Snapshot" warning.
        warned = self.state["snapshots"]
        days = self.snapshot_days()

        if not days:
            warned.clear()
            return

        ignored = self.ignored_vms()
        now = time.time()
        seen = set()

        for vm in vms:
            name = vm["name"]

            if name.lower() in ignored:
                continue

            seen.add(name)
            old = [
                s for s in vm.get("snapshots", [])
                if s["created"] is not None and now - s["created"] >= days * 86400
            ]

            if not old:
                if name in warned:
                    del warned[name]
                    await self.notification_service.recovered(
                        title=f"Snapshot Removed — {name}",
                        description=(
                            f"**{name}** has no snapshot older than "
                            f"{days:g} days any more."
                        ),
                    )
                continue

            if now - warned.get(name, 0) < 86400:
                continue

            warned[name] = now
            lines = "\n".join(f"• {s['name']} — {self.age(s['created'])}" for s in old)
            await self.notification_service.warning(
                title=f"Old Snapshot — {name}",
                description=(
                    f"**{name}** has {len(old)} snapshot{'s' if len(old) != 1 else ''} "
                    f"older than {days:g} days:\n{lines[:1500]}\n\n"
                    "A snapshot keeps growing and slows the VM down; "
                    "delete it in the ESXi host client when it is no longer needed."
                ),
            )

        # A VM that was deleted or is now ignored is forgotten quietly.
        for name in list(warned):
            if name not in seen:
                del warned[name]

    @staticmethod
    def _power(state: str) -> str:
        return {
            "poweredOn": "running",
            "poweredOff": "off",
            "suspended": "suspended",
        }.get(state, state)
