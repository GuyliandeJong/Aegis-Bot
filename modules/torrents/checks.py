"""Startup checks for qBittorrent and, when it is set up, Jackett."""

import os

import aiohttp

from core.startup_check import CHECK_TIMEOUT
from modules.torrents.qbittorrent_service import QBittorrentService


async def _check_qbittorrent() -> None:
    version = await QBittorrentService().get_version()

    if not version:
        raise RuntimeError("no version returned")


async def _check_jackett(base_url: str, api_key: str) -> None:
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


def checks() -> dict:
    found = {"qBittorrent": _check_qbittorrent()}

    # Jackett is optional: without it /torrent search is not available.
    base_url = os.getenv("JACKETT_URL", "").strip().rstrip("/")
    api_key = os.getenv("JACKETT_API_KEY", "").strip()

    if base_url and api_key:
        found["Jackett"] = _check_jackett(base_url, api_key)

    return found
