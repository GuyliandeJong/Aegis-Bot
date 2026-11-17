"""A minimal client for NUT (Network UPS Tools), over its plain text protocol."""

import asyncio
import os

from utils.logger import logger


class NutError(Exception):
    """The NUT server could not be reached or refused the request."""


class NutService:
    """
    Reads variables of one UPS from a NUT server (upsd).

    .env:
        NUT_HOST  address of the NUT server (required)
        NUT_PORT  port (default 3493)
        NUT_UPS   name of the UPS on that server (default "ups")

    Only "GET VAR" is used; upsd does not ask for a login for it.
    """

    TIMEOUT = 10

    def __init__(self):
        self.host = os.getenv("NUT_HOST", "").strip()
        self.ups = os.getenv("NUT_UPS", "ups").strip() or "ups"

        try:
            self.port = int(os.getenv("NUT_PORT", "3493") or 3493)
        except ValueError:
            self.port = 3493

    @property
    def configured(self) -> bool:
        return bool(self.host)

    async def read(self, names: list[str]) -> dict[str, str]:
        """
        The values of the given variables. A variable the UPS does not
        have is left out; NutError when the server cannot be reached or
        does not know the UPS.
        """
        try:
            return await asyncio.wait_for(self._read(names), timeout=self.TIMEOUT)
        except asyncio.TimeoutError:
            raise NutError(f"{self.host}:{self.port} did not answer in time.")
        except OSError as ex:
            raise NutError(f"{self.host}:{self.port} is not reachable ({type(ex).__name__}).")

    async def _read(self, names: list[str]) -> dict[str, str]:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        values = {}

        try:
            for name in names:
                writer.write(f"GET VAR {self.ups} {name}\n".encode())
                await writer.drain()
                line = (await reader.readline()).decode(errors="replace").strip()

                if not line:
                    raise NutError("The NUT server closed the connection.")

                if line.startswith("VAR "):
                    # VAR <ups> <name> "<value>"
                    _, _, rest = line.partition(f"{name} ")
                    values[name] = rest.strip().strip('"')
                elif line.startswith("ERR UNKNOWN-UPS"):
                    raise NutError(f"The NUT server has no UPS called `{self.ups}`.")
                elif line.startswith("ERR VAR-NOT-SUPPORTED"):
                    continue
                elif line.startswith("ERR"):
                    raise NutError(f"The NUT server answered: {line}")

            writer.write(b"LOGOUT\n")
            await writer.drain()
        finally:
            writer.close()

            try:
                await writer.wait_closed()
            except OSError as ex:
                logger.debug("[NutService] Closing failed: %s", ex)

        return values
