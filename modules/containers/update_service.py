"""
The request /bot update leaves for the host script that updates and
restarts the bot.

The bot runs in a container and cannot run commands on the server. It
writes a small request file in its data folder instead; a systemd path
unit on the host (scripts/install_update_service.sh) notices the file
at once, with no polling, and runs scripts/update_host.sh as the server's
own user. The file holds plain "key=value" lines:

    id=<unique id>
    branch=<branch name>
    time=<epoch seconds>
    by=<Discord user id>

The host script checks every value again, so a damaged or old file
does nothing.
"""

import os
import re
import secrets
import time

from config.paths import DATA_DIR


REQUEST_FILE = DATA_DIR / "update_request"

# The same rule as scripts/update_host.sh: a plain git branch name.
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def enabled() -> bool:
    """REMOTE_UPDATE is on: the host service is installed (see docs)."""
    value = os.getenv("REMOTE_UPDATE", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def valid_branch(name: str) -> bool:
    return bool(
        _BRANCH.match(name or "")
        and ".." not in name
        and not name.endswith(("/", ".lock", "."))
        and "//" not in name
    )


def write_request(branch: str, user_id: int) -> str:
    """
    Leave an update request for the host and return its id.

    The file is written in place and closed, which is what the path unit
    reacts to. ValueError for a branch name that is not valid.
    """
    if not valid_branch(branch):
        raise ValueError(f"Not a valid branch name: {branch!r}")

    request_id = f"{int(time.time())}-{secrets.token_hex(4)}"
    REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)

    with REQUEST_FILE.open("w") as file:
        file.write(
            f"id={request_id}\n"
            f"branch={branch}\n"
            f"time={int(time.time())}\n"
            f"by={int(user_id)}\n"
        )

    # The host user reads it; the container owns the folder.
    os.chmod(REQUEST_FILE, 0o644)

    return request_id
