"""
Which containers Watchtower updated, read from its log through Portainer.

Watchtower (containrrr/watchtower) keeps no history of its own, but it
logs every update at info level. One update session looks like this
(log format "logfmt"; "pretty" and "json" carry the same messages):

    msg="Found new jellyfin/jellyfin:latest image (8b2e1f0c4d3a)"
    msg="Stopping /jellyfin (1c9d2e3f4a5b) with SIGTERM"
    msg="Creating /jellyfin"
    msg="Session done" Failed=0 Scanned=28 Updated=1 notify=no

The messages come from Watchtower's source:
    pkg/container/client.go    HasNewImage:    "Found new %s image (%s)"
                               StopContainer:  "Stopping %s (%s) with %s"
                               StartContainer: "Creating %s"
    cmd/root.go                runUpdatesWithNotifications: "Session done"
The name is Docker's container name with its leading slash, the image
is the container's Config.Image with ":latest" added when it has no
tag (Container.ImageName in pkg/container/container.go).

Within a session every "Found new" line comes first (the stale check),
then every stop, then every "Creating" in dependency order
(internal/actions/update.go), so the two are not adjacent. A "Found
new" line names the image but not the container, so each recreated
container is matched to an image through its current image in
Docker's container list. A container recreated without a new image of
its own was restarted because a linked container was updated.

.env:
    WATCHTOWER_CONTAINER   name of Watchtower's container
                           (default watchtower)
"""

import os
import re
from datetime import datetime

from modules.containers.portainer_service import PortainerService
from utils.logger import logger


# Docker's timestamps=1 prefix: RFC 3339 with nanoseconds, then a space.
TIMESTAMP = re.compile(
    r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d) ?"
)

FOUND = re.compile(r"Found new (\S+) image \(")
CREATING = re.compile(r"Creating /([A-Za-z0-9][A-Za-z0-9_.-]*)")
SESSION_DONE = "Session done"


def container_name() -> str:
    return os.getenv("WATCHTOWER_CONTAINER", "watchtower").strip() or "watchtower"


def _timestamp(line: str) -> tuple[float | None, str]:
    """Split Docker's timestamp off a log line: (Unix time or None, rest)."""
    match = TIMESTAMP.match(line)

    if not match:
        return None, line

    base, fraction, zone = match.groups()

    try:
        when = datetime.fromisoformat(base + ("+00:00" if zone == "Z" else zone))
    except ValueError:
        return None, line

    return when.timestamp() + float("0" + (fraction or "")), line[match.end():]


def _image_name(image: str) -> str:
    """The image as Watchtower names it: ":latest" added when untagged."""
    return image if ":" in image else f"{image}:latest"


def parse(text: str, images: dict[str, str], since: float) -> list[dict]:
    """
    Every container recreated by Watchtower since a Unix time, oldest
    first: {"name", "image", "time", "linked"}.

    images maps container names to their current image (Docker's list);
    "image" is None when it cannot be told, and "linked" is True for a
    container restarted only because a linked container was updated.
    """
    updates = []
    found: list[str] = []
    created: list[tuple[str, float]] = []

    def close_session():
        for name, when in created:
            current = images.get(name) or ""
            # Docker shows an image ID once the tag has moved on.
            known = bool(current) and not current.startswith("sha256:")
            image, linked = None, False

            if known and _image_name(current) in found:
                image = _image_name(current)
            elif known:
                linked = True
            elif len(found) == 1:
                image = found[0]

            updates.append(
                {"name": name, "image": image, "time": when, "linked": linked}
            )

        found.clear()
        created.clear()

    for line in text.splitlines():
        when, message = _timestamp(line)

        if when is None or when < since:
            continue

        match = FOUND.search(message)

        if match:
            found.append(match.group(1))
            continue

        match = CREATING.search(message)

        if match:
            created.append((match.group(1), when))
            continue

        if SESSION_DONE in message:
            close_session()

    # A session still running, or one whose end was cut off.
    close_session()

    return updates


async def updates_since(since: float) -> list[dict] | None:
    """
    Containers Watchtower updated since a Unix time (see parse), or
    None when Portainer is not configured or Watchtower's container is
    not there. Other Portainer errors are raised.
    """
    service = PortainerService()

    if not service.configured:
        return None

    containers = await service.list_containers(fresh=True)
    wanted = container_name().lower()
    watchtower = next(
        (c for c in containers if c["name"].lower() == wanted), None
    )

    if watchtower is None:
        logger.info("[Watchtower] No container named %s.", container_name())
        return None

    text = await service.logs_since(watchtower["id"], since)
    images = {container["name"]: container["image"] for container in containers}

    return parse(text, images, since)
