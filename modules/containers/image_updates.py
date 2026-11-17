"""Finds running containers whose image has a newer version in its registry."""

import asyncio

from modules.containers.portainer_service import PortainerError, PortainerService
from utils.logger import logger


# Registry questions in flight at once.
CONCURRENCY = 4


async def outdated(service: PortainerService) -> dict:
    """
    Compare the running containers' images with their registries.

    The digest the registry has for an image's name:tag (asked through
    the Docker daemon) is compared with the digests the local image was
    pulled as. Images built here, pinned by digest, or whose registry
    does not answer are skipped, not reported.

    Returns {"outdated": [(image, [container names])], "checked": n,
    "skipped": n}, one entry per distinct image.
    """
    running = [
        container
        for container in await service.list_containers(fresh=True)
        if container["state"] == "running"
        and container["image"]
        and container.get("image_id")
    ]

    images: dict[str, dict] = {}

    for container in running:
        entry = images.setdefault(
            container["image"],
            {"image_id": container["image_id"], "names": []},
        )
        entry["names"].append(container["name"])

    limit = asyncio.Semaphore(CONCURRENCY)
    result = {"outdated": [], "checked": 0, "skipped": 0}

    async def check(image: str, entry: dict) -> None:
        # A digest in the name, or no name at all (an ID), cannot be older.
        if "@" in image or image.startswith("sha256:"):
            result["skipped"] += 1
            return

        async with limit:
            try:
                local = await service.local_digests(entry["image_id"])

                if not local:
                    result["skipped"] += 1
                    return

                remote = await service.registry_digest(image)
            except PortainerError as ex:
                logger.info("[ImageUpdates] %s skipped: %s", image, ex)
                result["skipped"] += 1
                return

        result["checked"] += 1

        if remote not in local:
            result["outdated"].append((image, sorted(entry["names"])))

    await asyncio.gather(*(check(image, entry) for image, entry in images.items()))
    result["outdated"].sort()

    return result
