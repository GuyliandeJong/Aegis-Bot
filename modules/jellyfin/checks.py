"""Startup check of every configured Jellyfin server."""

import aiohttp

from core.startup_check import CHECK_TIMEOUT
from modules.jellyfin.jellyfin_service import JellyfinService


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


def checks() -> dict:
    return {
        server.name: _check_jellyfin(server)
        for server in JellyfinService().servers
    }
