"""
Show what iDRAC reports about power supplies and power draw.

Run inside the container:  docker exec aegis-bot python scripts/redfish_power_check.py
Prints only names, health and watts; no serial numbers or addresses.
"""

import asyncio
import sys

# Run from /app, so the bot's own modules are found.
sys.path.insert(0, ".")

from redfish.client import RedfishClient
async def main():
    c = RedfishClient()
    try:
        chassis = await c.get_chassis()
        print("chassis links:", sorted(k for k in chassis if k in ("Power", "PowerSubsystem", "Thermal", "ThermalSubsystem")))
        p = await c.get_power()
        print("power keys:", sorted(p))
        print("supplies:", [{k: v for k, v in x.items() if k in ("Name", "MemberId", "Status", "PowerCapacityWatts", "LastPowerOutputWatts", "PowerInputWatts")} for x in p.get("PowerSupplies") or []])
        print("control:", [{k: v for k, v in x.items() if k in ("Name", "PowerConsumedWatts")} for x in p.get("PowerControl") or []])
    finally:
        await c.close()
asyncio.run(main())
