"""
Checks at startup that every service the bot depends on answers.

Without this, a wrong password or a stopped container only shows up
when someone runs a command that needs it. The result is logged, and
anything unreachable is reported once in the alert channel.
"""

import asyncio
import os

import aiohttp

from config.settings import settings
from redfish.client import RedfishClient
from services.jellyfin_service import JellyfinService
from services.qbittorrent_service import QBittorrentService
from utils.logger import logger


# Each service gets this long to answer.
CHECK_TIMEOUT = 15


async def _check_qbittorrent() -> None:
    version = await QBittorrentService().get_version()

    if not version:
        raise RuntimeError("no version returned")


async def _check_jackett() -> None:
    base_url = os.getenv("JACKETT_URL", "").strip().rstrip("/")
    api_key = os.getenv("JACKETT_API_KEY", "").strip()

    if not base_url or not api_key:
        raise RuntimeError("JACKETT_URL or JACKETT_API_KEY is not set")

    # The capabilities request needs a valid API key but searches
    # nothing.
    url = f"{base_url}/api/v2.0/indexers/all/results/torznab/api"

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT),
    ) as session:
        async with session.get(
            url,
            params={"apikey": api_key, "t": "caps"},
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")


async def _check_jellyfin(server) -> None:
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT),
    ) as session:
        async with session.get(
            f"{server.url}/System/Info",
            headers=JellyfinService._auth_headers(server),
        ) as response:
            if response.status == 401:
                raise RuntimeError("the API key was refused")

            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")


async def _check_idrac() -> None:
    if not getattr(settings, "IDRAC_HOST", ""):
        raise RuntimeError("IDRAC_HOST is not set")

    client = RedfishClient()

    try:
        await client.get_system()
    finally:
        await client.close()


def _describe(ex: BaseException) -> str:
    if isinstance(ex, asyncio.TimeoutError):
        return f"no answer within {CHECK_TIMEOUT} seconds"

    if isinstance(ex, aiohttp.ClientResponseError):
        if ex.status == 401:
            return "the login was refused"

        return f"HTTP {ex.status}"

    if isinstance(ex, aiohttp.ClientConnectorError):
        return "could not connect"

    # qBittorrent's errors end with the address that was asked; the
    # alert channel only needs the status.
    text = str(ex).split(", message=")[0].strip()

    return text[:200] if text else type(ex).__name__


async def run(notification_service) -> list[tuple[str, str]]:
    """
    Check every service and return the failures as (name, reason).

    Failures are posted to the alert channel as one message.
    """
    checks = {
        "qBittorrent": _check_qbittorrent(),
        "Jackett": _check_jackett(),
        "iDRAC": _check_idrac(),
    }

    for server in JellyfinService().servers:
        checks[server.name] = _check_jellyfin(server)

    results = await asyncio.gather(
        *(
            asyncio.wait_for(check, timeout=CHECK_TIMEOUT)
            for check in checks.values()
        ),
        return_exceptions=True,
    )

    failures = []

    for name, result in zip(checks, results):
        if isinstance(result, BaseException):
            reason = _describe(result)
            failures.append((name, reason))
            logger.warning(
                "[StartupCheck] %s is not reachable: %s",
                name,
                reason,
            )
        else:
            logger.info("[StartupCheck] %s answered.", name)

    if failures:
        await notification_service.warning(
            title="Services not reachable at startup",
            description=(
                "Commands that need these will fail until they are "
                "reachable again:\n"
                + "\n".join(
                    f"• **{name}**: {reason}"
                    for name, reason in failures
                )
            ),
        )

    return failures
