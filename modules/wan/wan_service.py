import asyncio

import aiohttp


class WanService:
    """
    Provides WAN connectivity and public IP information.

    Internet connectivity is tested against multiple independent
    endpoints so a temporary failure of one external service does
    not incorrectly indicate that the server has lost internet access.
    """

    IP_URL = "https://api.ipify.org"

    CONNECTIVITY_URLS = (
        "https://www.google.com/generate_204",
        "https://www.cloudflare.com/cdn-cgi/trace",
        "https://www.microsoft.com/",
    )

    async def get_ip(self) -> str:
        """
        Query the external IP service and return the public IPv4 address.
        """

        timeout = aiohttp.ClientTimeout(total=5)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                self.IP_URL
            ) as response:
                response.raise_for_status()

                ip_address = await response.text()

        return ip_address.strip()

    async def _check_endpoint(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> bool:
        """
        Check whether a single external endpoint is reachable.
        """

        try:
            async with session.get(
                url,
                allow_redirects=True,
            ) as response:

                return 200 <= response.status < 400

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ):
            return False

    async def is_connected(self) -> bool:
        """
        Determine whether the server currently has internet access.

        The connection is considered available when at least two of
        the configured external endpoints respond successfully.
        """

        timeout = aiohttp.ClientTimeout(total=5)

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            results = await asyncio.gather(
                *(
                    self._check_endpoint(
                        session,
                        url,
                    )
                    for url in self.CONNECTIVITY_URLS
                ),
                return_exceptions=False,
            )

        successful_checks = sum(results)

        return successful_checks >= 2