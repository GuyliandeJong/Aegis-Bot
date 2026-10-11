"""
Portainer client for managing Docker containers.

Portainer passes the Docker Engine API through under
/api/endpoints/<id>/docker/, so every call here is a plain Docker
container call that Portainer authenticates with an API access token.

Only the operations below are implemented. The same token could also
exec into or delete anything else, so nothing outside this file's
methods is ever sent to Portainer.

Settings are read from .env, like JackettService and JellyfinService:

    PORTAINER_URL               address reachable from the bot container
    PORTAINER_API_KEY           access token from My account in Portainer
    PORTAINER_ENDPOINT_ID       Docker environment ID
    PORTAINER_ALLOWED_USER_IDS  comma separated Discord user IDs
    PORTAINER_PROTECTED         comma separated container names that may
                                not be stopped, restarted, killed or removed
    PORTAINER_VERIFY_TLS        false for a self-signed certificate
"""

import asyncio
import json
import os
import re
import time

import aiohttp

from utils.logger import logger


# Docker's default grace period before a stop becomes a kill.
STOP_GRACE_SECONDS = 10

REQUEST_TIMEOUT = 30
ACTION_TIMEOUT = 60

# The container list is cached briefly so autocomplete, which Discord
# calls on every keystroke, does not hit Portainer each time.
LIST_CACHE_SECONDS = 5

GUARDED_ACTIONS = ("stop", "restart", "kill", "remove")

_list_cache: dict = {"at": 0.0, "data": None}


class PortainerError(Exception):
    """An error whose message is safe to show in Discord."""


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def demultiplex(data: bytes) -> bytes:
    """
    Remove Docker's stream framing from a log response.

    Containers without a TTY return frames of an 8 byte header
    (stream type, three zero bytes, big-endian length) and a payload.
    Anything that does not look like that is returned unchanged.
    """
    output = []
    position = 0

    while position + 8 <= len(data):
        header = data[position:position + 8]

        if header[0] not in (0, 1, 2) or header[1:4] != b"\x00\x00\x00":
            return data

        size = int.from_bytes(header[4:8], "big")
        output.append(data[position + 8:position + 8 + size])
        position += 8 + size

    return b"".join(output)


class PortainerService:
    """Container operations through the Portainer API."""

    def __init__(self):
        self.url = _env("PORTAINER_URL").rstrip("/")
        self.api_key = _env("PORTAINER_API_KEY")
        self.endpoint_id = _env("PORTAINER_ENDPOINT_ID")
        self.verify_tls = _env("PORTAINER_VERIFY_TLS").lower() not in (
            "false",
            "0",
            "no",
        )
        self.protected = {
            name.lower() for name in _split(_env("PORTAINER_PROTECTED"))
        }
        self.allowed_user_ids = set()

        for value in _split(_env("PORTAINER_ALLOWED_USER_IDS")):
            if value.isdigit():
                self.allowed_user_ids.add(int(value))

    # ------------------------------------------------------------
    # Configuration and access
    # ------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key and self.endpoint_id)

    def is_user_allowed(self, user_id: int) -> bool:
        """An empty list allows nobody."""
        return user_id in self.allowed_user_ids

    def protection_reason(self, container: dict) -> str | None:
        """
        Say why a container may not be stopped, restarted, killed or
        removed from Discord, or None when it may.

        Besides the names in PORTAINER_PROTECTED, the bot's own
        container and any Portainer container are always protected:
        stopping them would cut off this very control path.
        """
        name = container["name"].lower()

        if name in self.protected:
            return f"`{container['name']}` is protected."

        hostname = _env("HOSTNAME")

        if len(hostname) >= 12 and container["id"].startswith(hostname):
            return "That is the bot's own container."

        if container.get("image", "").lower().startswith("portainer/"):
            return "That is a Portainer container."

        return None

    # ------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> tuple[int, bytes]:
        if not self.configured:
            raise PortainerError(
                "Portainer is not configured. Set PORTAINER_URL, "
                "PORTAINER_API_KEY and PORTAINER_ENDPOINT_ID in .env."
            )

        url = (
            f"{self.url}/api/endpoints/{self.endpoint_id}/docker{path}"
        )
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "Aegis-Bot/1.0",
        }
        connector = aiohttp.TCPConnector(
            ssl=None if self.verify_tls else False
        )

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
            ) as session:
                async with session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                ) as response:
                    return response.status, await response.read()

        except asyncio.CancelledError:
            raise

        except asyncio.TimeoutError:
            raise PortainerError("Portainer did not answer in time.")

        except aiohttp.ClientError as ex:
            logger.warning("[Portainer] Request failed: %s", ex)
            raise PortainerError(f"Could not reach Portainer: {ex}")

    @staticmethod
    def _api_message(status: int, body: bytes) -> str:
        """Portainer's or Docker's own error text, passed on as is."""
        text = body.decode("utf-8", "replace").strip()

        try:
            data = json.loads(text)

            if isinstance(data, dict):
                message = data.get("message") or ""
                details = data.get("details") or ""
                text = " ".join(
                    part for part in (message, details) if part
                ) or text

        except ValueError:
            pass

        if status in (401, 403):
            text = f"{text} (check PORTAINER_API_KEY)".strip()

        return f"Portainer returned HTTP {status}: {text[:300]}"

    # ------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------

    async def list_containers(self, fresh: bool = False) -> list[dict]:
        """Every container, including stopped ones, sorted by name."""
        now = time.monotonic()

        if (
            not fresh
            and _list_cache["data"] is not None
            and now - _list_cache["at"] < LIST_CACHE_SECONDS
        ):
            return _list_cache["data"]

        status, body = await self._request(
            "GET",
            "/containers/json",
            params={"all": "1"},
        )

        if status != 200:
            raise PortainerError(self._api_message(status, body))

        try:
            raw = json.loads(body)
        except ValueError:
            raise PortainerError("Portainer returned an unreadable list.")

        containers = []

        for item in raw if isinstance(raw, list) else []:
            names = item.get("Names") or []

            if not names:
                continue

            status_text = item.get("Status") or ""
            health = None
            match = re.search(
                r"\((healthy|unhealthy|health: starting)\)",
                status_text,
            )

            if match:
                health = match.group(1).replace("health: ", "")

            exit_code = None
            match = re.match(r"Exited \((-?\d+)\)", status_text)

            if match:
                exit_code = int(match.group(1))

            containers.append(
                {
                    "id": item.get("Id") or "",
                    "name": names[0].lstrip("/"),
                    "image": item.get("Image") or "",
                    "state": item.get("State") or "",
                    "status": status_text,
                    "health": health,
                    "exit_code": exit_code,
                }
            )

        containers.sort(key=lambda c: c["name"].lower())
        _list_cache["at"] = now
        _list_cache["data"] = containers

        return containers

    @staticmethod
    def display_state(container: dict) -> str:
        """A short state label such as healthy or exited (137)."""
        state = container["state"]

        if state == "running":
            return container["health"] or "running"

        if state == "exited" and container["exit_code"] is not None:
            return f"exited ({container['exit_code']})"

        return state or "unknown"

    async def find_container(self, name: str) -> dict:
        """Resolve a container name to its record. Exact match only."""
        wanted = name.strip().lstrip("/").lower()

        for container in await self.list_containers(fresh=True):
            if container["name"].lower() == wanted:
                return container

        raise PortainerError(f"No container named `{name.strip()}`.")

    async def inspect(self, container_id: str) -> dict:
        """The details shown by /container info."""
        status, body = await self._request(
            "GET",
            f"/containers/{container_id}/json",
        )

        if status == 404:
            raise PortainerError("That container no longer exists.")

        if status != 200:
            raise PortainerError(self._api_message(status, body))

        try:
            data = json.loads(body)
        except ValueError:
            raise PortainerError("Portainer returned unreadable details.")

        state = data.get("State") or {}
        config = data.get("Config") or {}
        health = (state.get("Health") or {}).get("Status")

        return {
            "status": state.get("Status") or "unknown",
            "running": bool(state.get("Running")),
            "health": health,
            "started_at": state.get("StartedAt") or "",
            "finished_at": state.get("FinishedAt") or "",
            "exit_code": state.get("ExitCode"),
            "restart_count": data.get("RestartCount") or 0,
            "image": config.get("Image") or "",
            "tty": bool(config.get("Tty")),
        }

    async def logs(self, container_id: str, tail: int) -> str:
        """The last lines of a container's output."""
        details = await self.inspect(container_id)

        status, body = await self._request(
            "GET",
            f"/containers/{container_id}/logs",
            params={
                "stdout": "1",
                "stderr": "1",
                "tail": str(tail),
            },
        )

        if status != 200:
            raise PortainerError(self._api_message(status, body))

        if not details["tty"]:
            body = demultiplex(body)

        return body.decode("utf-8", "replace")

    # ------------------------------------------------------------
    # Changing
    # ------------------------------------------------------------

    def _guard(self, action: str, container: dict) -> None:
        if action not in GUARDED_ACTIONS:
            return

        reason = self.protection_reason(container)

        if reason:
            raise PortainerError(
                f"{reason} {action.capitalize()} is not allowed "
                "from Discord for it."
            )

    async def perform(self, action: str, container: dict) -> str:
        """
        Start, stop, restart or kill a container.

        Returns "done", or "unchanged" when the container was already
        in the requested state.
        """
        if action not in ("start", "stop", "restart", "kill"):
            raise PortainerError(f"Unsupported action: {action}.")

        self._guard(action, container)

        params = None

        if action in ("stop", "restart"):
            params = {"t": str(STOP_GRACE_SECONDS)}

        status, body = await self._request(
            "POST",
            f"/containers/{container['id']}/{action}",
            params=params,
            timeout=ACTION_TIMEOUT,
        )

        _list_cache["data"] = None

        if status == 204:
            return "done"

        if status == 304:
            return "unchanged"

        if status == 404:
            raise PortainerError("That container no longer exists.")

        raise PortainerError(self._api_message(status, body))

    async def remove(self, container: dict) -> None:
        """
        Remove a stopped container.

        A running container is refused, force is never used and
        volumes are never removed.
        """
        self._guard("remove", container)

        details = await self.inspect(container["id"])

        if details["running"]:
            raise PortainerError(
                f"`{container['name']}` is running. Stop it first."
            )

        status, body = await self._request(
            "DELETE",
            f"/containers/{container['id']}",
            params={"v": "0", "force": "0", "link": "0"},
            timeout=ACTION_TIMEOUT,
        )

        _list_cache["data"] = None

        if status == 204:
            return

        if status == 404:
            raise PortainerError("That container no longer exists.")

        raise PortainerError(self._api_message(status, body))
