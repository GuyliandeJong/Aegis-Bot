"""Alerts for public web services going down and TLS certificates expiring."""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.base_monitor import BaseMonitor
from modules.uptime import uptime_service
from utils.formatting import format_compact_duration
from utils.logger import logger


# Order of the certificate levels; an alert is sent when it rises.
CERTIFICATE_RANK = {"ok": 0, "warning": 1, "critical": 2, "expired": 3}


class UptimeMonitor(BaseMonitor):
    """
    Requests every URL in UPTIME_URLS every UPTIME_INTERVAL seconds
    (default 300).

    Down: no answer within UPTIME_TIMEOUT seconds (default 15), an
    invalid certificate or an HTTP status of 500 or more, for
    FAILURES_BEFORE_ALERT checks in a row. "Back Up" follows once,
    with how long it was down.

    Certificates of https URLs: a warning when fewer than
    UPTIME_CERT_DAYS days are left (default 14), critical below three
    days. Each level is reported once; a renewed certificate clears it.

    A URL that is up at its first check gives no alert. Does nothing
    when UPTIME_URLS is empty.
    """

    STATE_FILE = Path("/app/data/uptime_monitor_state.json")

    INTERVAL = 300
    TIMEOUT = 60

    # Failed checks in a row before "Service Down" is reported.
    FAILURES_BEFORE_ALERT = 2

    # Fewer days than this left on a certificate is critical.
    CERTIFICATE_CRITICAL_DAYS = 3

    # The checks replace it with one that trusts their own test CA.
    SSL_CONTEXT = None

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"urls": {}}
        self.load_state()

        # Read here rather than at import: .env is loaded after the
        # monitors are imported.
        self.INTERVAL = max(30, uptime_service.number("UPTIME_INTERVAL", 300))
        self.request_timeout = uptime_service.number("UPTIME_TIMEOUT", 15)

        # The request and the certificate read each get the full time
        # limit; the URLs are checked at the same time.
        self.TIMEOUT = self.request_timeout * 2 + 30

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[UptimeMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[UptimeMonitor] Failed to save state: %s", ex)

    async def check(self):
        urls = uptime_service.configured_urls()

        if not urls:
            return

        results = await asyncio.gather(
            *(
                uptime_service.check(url, self.request_timeout, self.SSL_CONTEXT)
                for url in urls
            )
        )

        known = self.state["urls"]

        # Forget URLs that were removed from UPTIME_URLS.
        for url in list(known):
            if url not in urls:
                del known[url]

        for url, result in zip(urls, results):
            entry = known.setdefault(
                url,
                {
                    "failures": 0,
                    "first_failure": None,
                    "down": False,
                    "reason": "",
                    "certificate": "ok",
                    "expires": None,
                },
            )

            if result["up"]:
                await self._up(url, entry, result)
            else:
                await self._down(url, entry, result)

        self.save_state()

    async def _down(self, url: str, entry: dict, result: dict) -> None:
        now = time.time()

        if not entry["failures"]:
            entry["first_failure"] = now

        entry["failures"] += 1
        entry["reason"] = result["reason"]
        logger.warning(
            "[UptimeMonitor] %s failed (%s in a row): %s",
            url,
            entry["failures"],
            result["reason"],
        )

        if entry["down"] or entry["failures"] < self.FAILURES_BEFORE_ALERT:
            return

        entry["down"] = True
        await self.notification_service.warning(
            title=f"Service Down — {uptime_service.host(url)}",
            description=(
                f"**URL**\n{url}\n\n"
                f"**Reason**\n{result['reason']}\n\n"
                f"Failed {entry['failures']} checks in a row "
                f"(every {format_compact_duration(self.INTERVAL)})."
            ),
        )

    async def _up(self, url: str, entry: dict, result: dict) -> None:
        if entry["down"]:
            down_for = time.time() - (entry["first_failure"] or time.time())
            await self.notification_service.recovered(
                title=f"Service Back Up — {uptime_service.host(url)}",
                description=(
                    f"**URL**\n{url}\n\n"
                    f"**Down for**\n{format_compact_duration(down_for)}\n\n"
                    f"**Answer**\nHTTP {result['status']}"
                ),
            )

        entry["failures"] = 0
        entry["first_failure"] = None
        entry["down"] = False
        entry["reason"] = ""

        if result["expires"] is not None:
            await self._certificate(url, entry, result["expires"])

    async def _certificate(self, url: str, entry: dict, expires: float) -> None:
        level = uptime_service.certificate_level(
            expires,
            time.time(),
            uptime_service.number("UPTIME_CERT_DAYS", 14),
            self.CERTIFICATE_CRITICAL_DAYS,
        )
        previous = entry.get("certificate", "ok")
        entry["certificate"] = level
        entry["expires"] = expires

        # Only a rise is reported. A renewed certificate drops back to
        # "ok", so its next approach to expiry is reported again.
        if CERTIFICATE_RANK.get(level, 0) <= CERTIFICATE_RANK.get(previous, 0):
            return

        days = max(0, (expires - time.time()) / 86400)
        date = datetime.fromtimestamp(expires, timezone.utc).strftime("%d %B %Y %H:%M UTC")
        description = (
            f"**URL**\n{url}\n\n"
            f"**Expires**\n{date} (in {days:.1f} days)\n\n"
            "The certificate should be renewed automatically; check the "
            "reverse proxy's certificate renewal."
        )
        title = f"Certificate Expiring — {uptime_service.host(url)}"

        if level == "warning":
            await self.notification_service.warning(title=title, description=description)
        else:
            await self.notification_service.critical(title=title, description=description)
