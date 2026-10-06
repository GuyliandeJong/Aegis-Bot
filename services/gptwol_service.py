import aiohttp

from config.settings import settings
from utils.logger import logger


class GPTWOLService:
    """
    Communicates with the locally hosted GPTWOL instance.

    GPTWOL is intentionally kept LAN-only and is not exposed to the
    internet. Aegis-Bot acts as the controlled interface between
    Discord and GPTWOL.

    GPTWOL base URL:
        http://192.0.2.10:5000

    The service currently uses the two endpoints exposed by GPTWOL:

        GET  /check_status
        POST /wol_or_sol_send
    """

    def __init__(self):
        # GPTWOL is an internal LAN service.
        #
        # The host and port are configurable through .env so they do
        # not need to be hard-coded throughout the application.
        self.base_url = (
            f"http://{settings.GPTWOL_HOST}:{settings.GPTWOL_PORT}"
        ).rstrip("/")

        # Keep GPTWOL requests short so a broken/unavailable container
        # never blocks Aegis-Bot for an extended period.
        self.timeout = aiohttp.ClientTimeout(
            total=settings.GPTWOL_TIMEOUT
        )

    async def check_status(
        self,
        ip_address: str,
        test_type: str,
    ) -> str:
        """
        Check whether a configured GPTWOL computer is awake.

        GPTWOL expects:
            GET /check_status?ip_address=<IP>&test_type=<test>

        Returns:
            "awake"
            "asleep"

        Raises:
            aiohttp.ClientError
            asyncio.TimeoutError
            ValueError
        """

        url = f"{self.base_url}/check_status"

        logger.debug(
            "[GPTWOLService] Checking status of %s using %s",
            ip_address,
            test_type,
        )

        async with aiohttp.ClientSession(
            timeout=self.timeout
        ) as session:
            async with session.get(
                url,
                params={
                    "ip_address": ip_address,
                    "test_type": test_type,
                },
            ) as response:

                response.raise_for_status()

                status = (
                    await response.text()
                ).strip().lower()

        if status not in {
            "awake",
            "asleep",
        }:
            raise ValueError(
                f"GPTWOL returned an unexpected status: {status}"
            )

        logger.debug(
            "[GPTWOLService] %s is %s",
            ip_address,
            status,
        )

        return status

    async def send_wol_or_sol(
        self,
        mac_address: str,
    ) -> str:
        """
        Tell GPTWOL to toggle the configured computer.

        GPTWOL decides whether to send:

            Wake-on-LAN
        or
            Sleep-on-LAN

        based on the computer's current status.

        GPTWOL expects:

            POST /wol_or_sol_send

        with:

            mac_address=<MAC>

        Returns the resulting GPTWOL HTML response.

        Raises:
            aiohttp.ClientError
            asyncio.TimeoutError
        """

        url = f"{self.base_url}/wol_or_sol_send"

        logger.info(
            "[GPTWOLService] Sending Wake/Sleep request for %s",
            mac_address,
        )

        async with aiohttp.ClientSession(
            timeout=self.timeout
        ) as session:
            async with session.post(
                url,
                data={
                    "mac_address": mac_address,
                },
            ) as response:

                response.raise_for_status()

                result = await response.text()

        logger.info(
            "[GPTWOLService] Wake/Sleep request accepted for %s",
            mac_address,
        )

        return result