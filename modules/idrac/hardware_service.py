"""Power supply and fan health from iDRAC's Redfish API."""

import asyncio
from datetime import datetime, timezone

from modules.idrac.redfish_client import RedfishClient


# Redfish States meaning nothing is installed in that slot.
EMPTY_STATES = {"absent"}


def _health(member: dict) -> str:
    status = member.get("Status") or {}
    return str(status.get("Health") or "Unknown")


def _installed(member: dict) -> bool:
    status = member.get("Status") or {}
    return str(status.get("State") or "").lower() not in EMPTY_STATES


def power_supplies(power: dict) -> dict[str, dict]:
    """Installed power supplies by name, with health and detail."""
    result = {}

    for index, member in enumerate(power.get("PowerSupplies") or []):
        if not isinstance(member, dict) or not _installed(member):
            continue

        name = str(
            member.get("Name")
            or f"Power Supply {member.get('MemberId') or index + 1}"
        )

        details = []
        capacity = member.get("PowerCapacityWatts")
        output = member.get("LastPowerOutputWatts")

        if isinstance(output, (int, float)):
            details.append(f"{output:.0f} W out")

        if isinstance(capacity, (int, float)):
            details.append(f"{capacity:.0f} W rated")

        result[name] = {
            "kind": "Power supply",
            "health": _health(member),
            "detail": ", ".join(details),
        }

    return result


def fans(thermal: dict) -> dict[str, dict]:
    """Installed fans by name, with health and speed."""
    result = {}

    for index, member in enumerate(thermal.get("Fans") or []):
        if not isinstance(member, dict) or not _installed(member):
            continue

        name = str(
            member.get("Name")
            or member.get("FanName")
            or f"Fan {index + 1}"
        )

        reading = member.get("Reading")
        unit = member.get("ReadingUnits") or "RPM"

        result[name] = {
            "kind": "Fan",
            "health": _health(member),
            "detail": (
                f"{reading} {unit}"
                if isinstance(reading, (int, float))
                else ""
            ),
        }

    return result


def voltages(power: dict) -> dict[str, dict]:
    """
    Voltage sensors by name, with health.

    On a server whose power supply iDRAC cannot read, these "power
    good" sensors on the system board are the only sign of a power
    problem. Most report no reading, only a health.
    """
    result = {}

    for index, member in enumerate(power.get("Voltages") or []):
        if not isinstance(member, dict) or not _installed(member):
            continue

        status = member.get("Status") or {}

        # A sensor that is switched off has no meaningful health.
        if str(status.get("State") or "Enabled").lower() != "enabled":
            continue

        reading = member.get("ReadingVolts")

        result[str(member.get("Name") or f"Voltage {index + 1}")] = {
            "kind": "Voltage",
            "health": _health(member),
            "detail": (
                f"{reading} V"
                if isinstance(reading, (int, float))
                else ""
            ),
        }

    return result


def power_draw(power: dict) -> float | None:
    """Watts the whole server draws, when reported."""
    for control in power.get("PowerControl") or []:
        if not isinstance(control, dict):
            continue

        watts = control.get("PowerConsumedWatts")

        if isinstance(watts, (int, float)):
            return float(watts)

    return None


def _timestamp(value) -> datetime | None:
    """A Redfish date-time as an aware datetime; no zone means UTC."""
    if not isinstance(value, str) or not value:
        return None

    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    return moment


def log_entries(members: list) -> list[dict]:
    """
    Readable log entries from the Members of a LogEntry collection.

    Each is {id, created (aware datetime), severity ("OK", "Warning"
    or "Critical"), message, sensor}. Entries without a readable
    Created time are left out, because newness is judged by it.
    """
    result = []

    for member in members or []:
        if not isinstance(member, dict):
            continue

        created = _timestamp(member.get("Created"))

        if created is None:
            continue

        # Anything that is not one of the three DMTF values counts as
        # informational rather than as a problem.
        severity = {
            "warning": "Warning",
            "critical": "Critical",
        }.get(str(member.get("Severity") or "").lower(), "OK")

        result.append({
            "id": str(member.get("Id") or ""),
            "created": created,
            "severity": severity,
            "message": " ".join(str(member.get("Message") or "").split()),
            "sensor": str(member.get("SensorType") or ""),
        })

    return result


class HardwareService:
    async def get_hardware_health(self) -> dict:
        """
        Return {"components": {name: {kind, health, detail}},
        "power_watts": float | None}.
        """
        client = RedfishClient()

        try:
            power, thermal = await asyncio.gather(
                client.get_power(),
                client.get_thermal(),
            )
        finally:
            await client.close()

        return {
            "components": {
                **power_supplies(power),
                **fans(thermal),
                **voltages(power),
            },
            "power_watts": power_draw(power),
        }

    # Pages of the event log read per check. iDRAC answers 50 entries a
    # page; the System Event Log is much smaller than that limit.
    MAX_LOG_PAGES = 20

    async def get_event_log(self, log_service: str = "Sel") -> list[dict]:
        """
        Every entry of an iDRAC log service (default the System Event
        Log), following Members@odata.nextLink. Raises
        aiohttp.ClientResponseError when iDRAC refuses, e.g. 404 when
        the log does not exist.
        """
        client = RedfishClient()
        members = []

        try:
            for _ in range(self.MAX_LOG_PAGES):
                page = await client.get_log_entries(
                    log_service,
                    skip=len(members),
                )
                batch = page.get("Members") or []
                members.extend(batch)

                if not batch or not page.get("Members@odata.nextLink"):
                    break
        finally:
            await client.close()

        return log_entries(members)
