"""Alerts for disks that report failing health through SMART."""

import asyncio
import json
import os
import shutil

from config.paths import DATA_DIR
from core.base_monitor import BaseMonitor
from utils.logger import logger


class SmartMonitor(BaseMonitor):
    """
    Runs smartctl on the disks in SMART_DEVICES once an hour.

    - Health: SMART's own verdict failing gives a critical "Disk Health
      Failing", once; "Disk Health OK Again" follows if it passes.
    - SATA/SAS disks: pending or uncorrectable sectors give a warning
      once (until they are gone); reallocated sectors that grew since
      the last check give a warning each time. The first reading is
      the baseline, so a disk with old reallocated sectors is not
      reported for them.
    - NVMe disks: any critical warning flag is critical; wear at or
      above SMART_NVME_WEAR percent is a warning once; growing media
      errors a warning each time.
    - A disk that cannot be read three checks in a row gives one
      warning; a sleeping disk is skipped, not woken.

    smartctl (smartmontools) is in the image, but the container must be
    given the devices and the capability to read them; see the comments
    in docker-compose.example.yml. Without smartctl the monitor does
    nothing and says so once in the log.

    .env:
        SMART_DEVICES    devices to check, comma separated
                         (/dev/sda,/dev/nvme0n1); empty: off
        SMART_NVME_WEAR  percent of NVMe life used that warns (default 90)
    """

    STATE_FILE = DATA_DIR / "smart_monitor_state.json"

    INTERVAL = 3600
    TIMEOUT = 300

    UNREADABLE_BEFORE_ALERT = 3

    # ATA attribute ids
    REALLOCATED, PENDING, UNCORRECTABLE = 5, 197, 198

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {}
        self.missing_logged = False
        self.load_state()

    @staticmethod
    def devices() -> list[str]:
        value = os.getenv("SMART_DEVICES", "")
        return [part.strip() for part in value.split(",") if part.strip()]

    @staticmethod
    def wear_limit() -> int:
        try:
            return max(1, int(os.getenv("SMART_NVME_WEAR", "90")))
        except ValueError:
            return 90

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state = data
        except Exception as ex:
            logger.error("[SmartMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[SmartMonitor] Failed to save state: %s", ex)

    async def _read(self, executable: str, device: str) -> dict | None:
        """smartctl's JSON for one device, or None when it cannot be read."""
        try:
            process = await asyncio.create_subprocess_exec(
                executable, "--json", "-H", "-A", "-n", "standby", device,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except (asyncio.TimeoutError, OSError) as ex:
            logger.warning("[SmartMonitor] %s: smartctl failed: %s", device, type(ex).__name__)
            return None

        try:
            data = json.loads(out)
        except ValueError:
            return None

        return data if isinstance(data, dict) else None

    @staticmethod
    def _standby(data: dict) -> bool:
        messages = (data.get("smartctl") or {}).get("messages") or []
        return any("STANDBY" in str(m.get("string", "")).upper() for m in messages)

    @staticmethod
    def _attributes(data: dict) -> dict[int, int]:
        table = (data.get("ata_smart_attributes") or {}).get("table") or []
        return {
            int(a["id"]): int((a.get("raw") or {}).get("value") or 0)
            for a in table
            if "id" in a
        }

    async def check(self):
        devices = self.devices()

        if not devices:
            return

        executable = shutil.which("smartctl")

        if executable is None:
            if not self.missing_logged:
                self.missing_logged = True
                logger.warning(
                    "[SmartMonitor] SMART_DEVICES is set but smartctl is not installed."
                )
            return

        for device in devices:
            data = await self._read(executable, device)
            await self._device(device, data)

        self.save_state()

    async def _device(self, device: str, data: dict | None) -> None:
        state = self.state.setdefault(device, {"unreadable": 0})

        if data is None or (
            "smart_status" not in data
            and "ata_smart_attributes" not in data
            and "nvme_smart_health_information_log" not in data
        ):
            if data is not None and self._standby(data):
                return

            state["unreadable"] = state.get("unreadable", 0) + 1

            if state["unreadable"] == self.UNREADABLE_BEFORE_ALERT:
                await self.notification_service.warning(
                    title="SMART Not Readable",
                    description=(
                        f"`{device}` could not be read for "
                        f"{self.UNREADABLE_BEFORE_ALERT} checks in a row. Check "
                        "that the container has the device and the capability "
                        "to read it (docker-compose.example.yml) and that "
                        "smartctl supports the disk."
                    ),
                )
            return

        state["unreadable"] = 0
        model = data.get("model_name") or "unknown model"
        temperature = (data.get("temperature") or {}).get("current")
        label = f"`{device}` ({model}" + (f", {temperature}°C)" if temperature is not None else ")")

        passed = (data.get("smart_status") or {}).get("passed")

        if passed is False and not state.get("failed"):
            state["failed"] = True
            await self.notification_service.critical(
                title="Disk Health Failing",
                description=f"{label} reports failing SMART health. Back up its data and replace it.",
            )
        elif passed is True and state.get("failed"):
            state["failed"] = False
            await self.notification_service.recovered(
                title="Disk Health OK Again",
                description=f"{label} reports passing SMART health again.",
            )

        nvme = data.get("nvme_smart_health_information_log")

        if nvme:
            await self._nvme(device, label, state, nvme)
        else:
            await self._ata(device, label, state, self._attributes(data))

    async def _ata(self, device, label, state, attributes) -> None:
        reallocated = attributes.get(self.REALLOCATED)
        bad = attributes.get(self.PENDING, 0) + attributes.get(self.UNCORRECTABLE, 0)

        if bad and not state.get("bad"):
            state["bad"] = True
            await self.notification_service.warning(
                title="Disk Has Bad Sectors",
                description=(
                    f"{label} has {attributes.get(self.PENDING, 0)} pending and "
                    f"{attributes.get(self.UNCORRECTABLE, 0)} uncorrectable sectors."
                ),
            )
        elif not bad:
            state["bad"] = False

        if reallocated is None:
            return

        previous = state.get("reallocated")
        state["reallocated"] = reallocated

        if previous is not None and reallocated > previous:
            await self.notification_service.warning(
                title="Disk Reallocated Sectors Increasing",
                description=f"{label} went from {previous} to {reallocated} reallocated sectors.",
            )

    async def _nvme(self, device, label, state, log) -> None:
        if int(log.get("critical_warning") or 0) and not state.get("critical"):
            state["critical"] = True
            await self.notification_service.critical(
                title="NVMe Critical Warning",
                description=f"{label} reports critical warning flags ({log.get('critical_warning')}).",
            )
        elif not int(log.get("critical_warning") or 0):
            state["critical"] = False

        used = int(log.get("percentage_used") or 0)

        if used >= self.wear_limit() and not state.get("worn"):
            state["worn"] = True
            await self.notification_service.warning(
                title="NVMe Wear High",
                description=f"{label} has used {used}% of its rated life.",
            )
        elif used < self.wear_limit():
            state["worn"] = False

        errors = int(log.get("media_errors") or 0)
        previous = state.get("media_errors")
        state["media_errors"] = errors

        if previous is not None and errors > previous:
            await self.notification_service.warning(
                title="NVMe Media Errors Increasing",
                description=f"{label} went from {previous} to {errors} media errors.",
            )
