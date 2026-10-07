"""qBittorrent Web API service."""

import aiohttp

from config.settings import settings
from utils.logger import logger


class QBittorrentService:
    """Asynchronous qBittorrent Web API client."""

    def __init__(self):
        self.base_url = (
            f"http://{settings.QBITTORRENT_HOST}:{settings.QBITTORRENT_PORT}"
        ).rstrip("/")

        self.api_url = f"{self.base_url}/api/v2"

        self.timeout = aiohttp.ClientTimeout(
            total=settings.QBITTORRENT_TIMEOUT
        )

        self.session: aiohttp.ClientSession | None = None

    async def connect(self) -> None:
        """Create the HTTP session and authenticate with qBittorrent."""

        if self.session is not None and not self.session.closed:
            return

        logger.info(
            "[QBittorrentService] Connecting to qBittorrent at %s",
            self.base_url,
        )

        self.session = aiohttp.ClientSession(
            timeout=self.timeout,
            cookie_jar=aiohttp.CookieJar(
                unsafe=True,
            ),
            headers={
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
            },
        )

        try:
            async with self.session.post(
                f"{self.api_url}/auth/login",
                data={
                    "username": settings.QBITTORRENT_USERNAME,
                    "password": settings.QBITTORRENT_PASSWORD,
                },
            ) as response:

                if response.status not in {
                    200,
                    204,
                }:
                    text = await response.text()

                    raise RuntimeError(
                        "qBittorrent authentication failed: "
                        f"{response.status} {text}"
                    )

                logger.info(
                    "[QBittorrentService] Successfully authenticated."
                )

        except Exception:
            await self.close()
            raise

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
    ):
        url = f"{self.api_url}{endpoint}"

        logger.debug(
            "[QBittorrentService] %s %s",
            method,
            url,
        )

        # qBittorrent answers 403 once its login cookie is no longer
        # valid, for example after qBittorrent restarts. Long-lived
        # users of this service (the monitors) would otherwise fail
        # forever, so log in again and retry the request once.
        for attempt in range(2):
            await self.connect()

            if self.session is None:
                raise RuntimeError(
                    "qBittorrent session was not initialized."
                )

            async with self.session.request(
                method,
                url,
                params=params,
                data=data,
            ) as response:

                if response.status == 403 and attempt == 0:
                    logger.info(
                        "[QBittorrentService] Session rejected; "
                        "logging in again."
                    )
                    retry = True

                else:
                    retry = False

                    if response.status >= 400:
                        text = await response.text()

                        raise RuntimeError(
                            "qBittorrent API request failed: "
                            f"{response.status}, "
                            f"message='{text}', "
                            f"url='{url}'"
                        )

                    return await self._parse_response(
                        response
                    )

            if retry:
                await self.close()

    @staticmethod
    async def _parse_response(
        response: aiohttp.ClientResponse,
    ):
        if response.status == 204:
            return None

        text = await response.text()

        if not text:
            return None

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()

        if "application/json" in content_type:
            return await response.json()

        return text

    async def get_version(self) -> str:
        """Return the qBittorrent version."""

        return await self._request(
            "GET",
            "/app/version",
        )

    async def get_preferences(self) -> dict:
        """Return qBittorrent application preferences."""

        return await self._request(
            "GET",
            "/app/preferences",
        )

    async def get_transfer_info(self) -> dict:
        """Return global transfer information."""

        return await self._request(
            "GET",
            "/transfer/info",
        )

    async def get_torrents(
        self,
        category: str | None = None,
        filter_name: str | None = None,
    ) -> list[dict]:
        """Return torrents, optionally filtered by category/state."""

        params = {}

        if category:
            params["category"] = category

        if filter_name:
            params["filter"] = filter_name

        result = await self._request(
            "GET",
            "/torrents/info",
            params=params,
        )

        if not isinstance(
            result,
            list,
        ):
            raise RuntimeError(
                "qBittorrent returned an unexpected "
                "torrent list response."
            )

        return result

    async def get_torrent_info(
        self,
        torrent_hash: str,
    ) -> list[dict]:
        """Return detailed information for one torrent."""

        result = await self._request(
            "GET",
            "/torrents/info",
            params={
                "hashes": torrent_hash,
            },
        )

        if not isinstance(
            result,
            list,
        ):
            raise RuntimeError(
                "qBittorrent returned an unexpected "
                "torrent info response."
            )

        return result

    async def get_categories(self) -> dict:
        """Return configured qBittorrent categories."""

        result = await self._request(
            "GET",
            "/torrents/categories",
        )

        if not isinstance(
            result,
            dict,
        ):
            raise RuntimeError(
                "qBittorrent returned an unexpected "
                "categories response."
            )

        return result

    async def get_storage_path(
        self,
        category: str,
    ) -> str | None:
        """Return the storage path configured for a category."""

        categories = await self.get_categories()

        category_info = categories.get(
            category
        )

        if not category_info:
            return None

        save_path = category_info.get(
            "savePath"
        )

        if not save_path:
            return None

        return str(save_path)

    async def add_torrent(
        self,
        magnet_url: str,
        category: str,
    ) -> str | None:
        """Add a magnet link to qBittorrent."""

        logger.info(
            "[QBittorrentService] Adding magnet to category '%s'.",
            category,
        )

        result = await self._request(
            "POST",
            "/torrents/add",
            data={
                "urls": magnet_url,
                "category": category,
            },
        )

        if not isinstance(
            result,
            dict,
        ):
            logger.warning(
                "[QBittorrentService] Unexpected add-torrent response: %r",
                result,
            )

            return None

        added_torrent_ids = result.get(
            "added_torrent_ids"
        )

        if not added_torrent_ids:
            logger.warning(
                "[QBittorrentService] qBittorrent did not return "
                "an added torrent ID: %r",
                result,
            )

            return None

        torrent_hash = str(
            added_torrent_ids[0]
        )

        logger.info(
            "[QBittorrentService] Torrent added successfully: %s",
            torrent_hash,
        )

        return torrent_hash

    async def pause_torrent(
        self,
        torrent_hash: str,
    ):
        """Pause one torrent."""

        logger.info(
            "[QBittorrentService] Pausing torrent %s.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/stop",
            data={
                "hashes": torrent_hash,
            },
        )

    async def resume_torrent(
        self,
        torrent_hash: str,
    ):
        """Resume one torrent."""

        logger.info(
            "[QBittorrentService] Resuming torrent %s.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/start",
            data={
                "hashes": torrent_hash,
            },
        )

    async def recheck_torrent(
        self,
        torrent_hash: str,
    ):
        """Force qBittorrent to recheck one torrent."""

        logger.info(
            "[QBittorrentService] Rechecking torrent %s.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/recheck",
            data={
                "hashes": torrent_hash,
            },
        )

    async def reannounce_torrent(
        self,
        torrent_hash: str,
    ):
        """Force qBittorrent to reannounce one torrent to its trackers."""

        logger.info(
            "[QBittorrentService] Reannouncing torrent %s.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/reannounce",
            data={
                "hashes": torrent_hash,
            },
        )

    async def increase_priority(
        self,
        torrent_hash: str,
    ):
        """Increase the queue priority of one torrent."""

        logger.info(
            "[QBittorrentService] Increasing priority for torrent %s.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/increasePrio",
            data={
                "hashes": torrent_hash,
            },
        )

    async def decrease_priority(
        self,
        torrent_hash: str,
    ):
        """Decrease the queue priority of one torrent."""

        logger.info(
            "[QBittorrentService] Decreasing priority for torrent %s.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/decreasePrio",
            data={
                "hashes": torrent_hash,
            },
        )

    async def top_priority(
        self,
        torrent_hash: str,
    ):
        """Move one torrent to the top of the download queue."""

        logger.info(
            "[QBittorrentService] Moving torrent %s to top priority.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/topPrio",
            data={
                "hashes": torrent_hash,
            },
        )

    async def bottom_priority(
        self,
        torrent_hash: str,
    ):
        """Move one torrent to the bottom of the download queue."""

        logger.info(
            "[QBittorrentService] Moving torrent %s to bottom priority.",
            torrent_hash,
        )

        await self._request(
            "POST",
            "/torrents/bottomPrio",
            data={
                "hashes": torrent_hash,
            },
        )

    async def delete_torrent(
        self,
        torrent_hash: str,
        delete_files: bool = False,
    ):
        """Delete one torrent, optionally deleting its files."""

        logger.info(
            "[QBittorrentService] Deleting torrent %s "
            "(delete_files=%s).",
            torrent_hash,
            delete_files,
        )

        await self._request(
            "POST",
            "/torrents/delete",
            data={
                "hashes": torrent_hash,
                "deleteFiles": (
                    "true"
                    if delete_files
                    else "false"
                ),
            },
        )

    async def close(self):
        """Close the HTTP session."""

        if (
            self.session is not None
            and not self.session.closed
        ):
            await self.session.close()

        self.session = None