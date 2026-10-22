"""
Checks at startup that every service the bot depends on answers.

Without this, a wrong password or a stopped container only shows up
when someone runs a command that needs it. The result is logged, and
anything unreachable is reported once in the alert channel.

The checks come from the enabled modules: a module's startup_checks
function returns {name: coroutine} (see core/modules.py).
"""

import asyncio

import aiohttp

from core import modules
from utils.logger import logger


# Each service gets this long to answer.
CHECK_TIMEOUT = 15


def _describe(ex: BaseException) -> str:
    if isinstance(ex, asyncio.TimeoutError):
        return f"no answer within {CHECK_TIMEOUT} seconds"

    if isinstance(ex, aiohttp.ClientResponseError):
        if ex.status == 401:
            return "the login was refused"

        return f"HTTP {ex.status}"

    if isinstance(ex, aiohttp.ClientConnectorError):
        return "could not connect"

    # qBittorrent's errors end with the address that was asked; the
    # alert channel only needs the status.
    text = str(ex).split(", message=")[0].strip()

    return text[:200] if text else type(ex).__name__


def module_checks() -> dict:
    """{name: coroutine} from every enabled module that has checks."""
    checks = {}

    for module in modules.discover():
        if not module.startup_checks or not modules.enabled(module.name):
            continue

        try:
            checks.update(modules.resolve(module.startup_checks)())
        except Exception:
            logger.exception("[StartupCheck] Checks of %s failed to start.", module.name)

    return checks


async def run(notification_service, checks: dict | None = None) -> list[tuple[str, str]]:
    """
    Check every service and return the failures as (name, reason).

    Failures are posted to the alert channel as one message.
    """
    if checks is None:
        checks = module_checks()

    results = await asyncio.gather(
        *(
            asyncio.wait_for(check, timeout=CHECK_TIMEOUT)
            for check in checks.values()
        ),
        return_exceptions=True,
    )

    failures = []

    for name, result in zip(checks, results):
        if isinstance(result, BaseException):
            reason = _describe(result)
            failures.append((name, reason))
            logger.warning(
                "[StartupCheck] %s is not reachable: %s",
                name,
                reason,
            )
        else:
            logger.info("[StartupCheck] %s answered.", name)

    if failures:
        await notification_service.warning(
            title="Services not reachable at startup",
            description=(
                "Commands that need these will fail until they are "
                "reachable again:\n"
                + "\n".join(
                    f"• **{name}**: {reason}"
                    for name, reason in failures
                )
            ),
        )

    return failures
