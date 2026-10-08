"""Power supply and fan health from iDRAC's Redfish API."""

import asyncio

from redfish.client import RedfishClient


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
