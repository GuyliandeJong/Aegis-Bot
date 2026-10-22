"""Startup check of the iDRAC's Redfish API."""

from modules.idrac.redfish_client import RedfishClient


async def _check_idrac() -> None:
    client = RedfishClient()

    try:
        await client.get_system()
    finally:
        await client.close()


def checks() -> dict:
    return {"iDRAC": _check_idrac()}
