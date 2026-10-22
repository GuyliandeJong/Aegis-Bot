"""
Restart containers after an internet outage.

Some containers keep a tunnel open and do not recover by themselves
when the connection drops; newt, the Pangolin tunnel client, is one.
After the WAN monitor sees the internet come back, the containers in
WAN_RECOVERY_RESTART are restarted through Portainer, the same way as
/container restart, and the result is posted.

.env:
    WAN_RECOVERY_RESTART      containers to restart, comma separated
                              (empty: nothing is restarted)
    WAN_RECOVERY_MIN_OUTAGE   only after an outage of at least this many
                              seconds (default 60), also when it was too
                              short for a "WAN Connection Lost" alert
    WAN_RECOVERY_DELAY        seconds to wait after recovery before
                              restarting (default 30), so the connection
                              has settled

The containers must not be in PORTAINER_PROTECTED, which blocks
restarts.
"""

import asyncio
import os

from core import activity_log
from modules.containers.portainer_service import PortainerError, PortainerService
from utils.logger import logger


_tasks: set[asyncio.Task] = set()


def containers() -> list[str]:
    value = os.getenv("WAN_RECOVERY_RESTART", "")
    return [name.strip() for name in value.split(",") if name.strip()]


def _seconds(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def min_outage() -> int:
    return _seconds("WAN_RECOVERY_MIN_OUTAGE", 60)


def delay() -> int:
    return _seconds("WAN_RECOVERY_DELAY", 30)


def start(notification_service, outage_seconds: float) -> bool:
    """
    Schedule the restarts in the background when they apply.

    Returns whether anything was scheduled; never blocks the monitor.
    """
    names = containers()

    if not names or outage_seconds < min_outage():
        return False

    task = asyncio.create_task(restart(notification_service, names, outage_seconds))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def restart(notification_service, names: list[str], outage_seconds: float) -> None:
    await asyncio.sleep(delay())

    service = PortainerService()
    done, failed = [], []

    for name in names:
        try:
            container = await service.find_container(name)
            await service.perform("restart", container)
            done.append(name)
            activity_log.record("wan_restart", container=name, ok=True)
            logger.info("[WanRecovery] Restarted %s after the outage.", name)
        except PortainerError as ex:
            failed.append((name, str(ex)))
            activity_log.record("wan_restart", container=name, ok=False)
            logger.warning("[WanRecovery] Could not restart %s: %s", name, ex)
        except Exception as ex:
            failed.append((name, "unexpected error, see the bot log"))
            activity_log.record("wan_restart", container=name, ok=False)
            logger.exception("[WanRecovery] Restarting %s failed: %s", name, ex)

    minutes = int(outage_seconds // 60)
    seconds = int(outage_seconds % 60)
    lines = []

    if done:
        lines.append("Restarted " + ", ".join(f"`{n}`" for n in done) + ".")

    for name, reason in failed:
        lines.append(f"`{name}`: {reason}")

    description = (
        f"After the internet outage of `{minutes}m {seconds}s`:\n\n"
        + "\n".join(lines)
    )

    try:
        if failed:
            await notification_service.warning(
                title="Restart After Outage Failed",
                description=description
                + "\n\nRestart it by hand with `/container restart`.",
            )
        else:
            await notification_service.success(
                title="Restarted After Outage",
                description=description,
            )
    except Exception:
        logger.exception("[WanRecovery] Could not post the result.")
