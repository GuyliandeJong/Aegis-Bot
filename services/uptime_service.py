"""
Check whether public web services answer, and when their TLS
certificate expires.

.env:
    UPTIME_URLS     full URLs, comma separated (https://...); empty: off
    UPTIME_TIMEOUT  seconds one request may take (default 15)

A URL counts as up when it answers with a status below 500. A login
page answering 401 or 403, or a redirect, is up: the service is there.
A failed connection, a timeout, an invalid certificate or a 5xx answer
counts as down.
"""

import asyncio
import os
import ssl
from urllib.parse import urlsplit

import aiohttp


def configured_urls() -> list[str]:
    return [
        url.strip()
        for url in os.getenv("UPTIME_URLS", "").split(",")
        if url.strip()
    ]


def host(url: str) -> str:
    """The host name of a URL, used in alert titles."""
    return urlsplit(url).hostname or url


def number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def certificate_level(
    expires: float,
    now: float,
    warning_days: float,
    critical_days: float = 3,
) -> str:
    """
    "ok", "warning" (fewer than warning_days left), "critical" (fewer
    than critical_days left) or "expired".
    """
    left = expires - now

    if left <= 0:
        return "expired"

    if left < critical_days * 86400:
        return "critical"

    if left < warning_days * 86400:
        return "warning"

    return "ok"


def _reason(ex: Exception) -> str:
    """A short, readable reason for a failed request."""
    if isinstance(ex, asyncio.TimeoutError):
        return "no answer within the time limit"

    if isinstance(ex, aiohttp.ClientConnectorCertificateError):
        error = ex.certificate_error
        text = getattr(error, "verify_message", "") or str(error)
        return f"certificate not valid: {text}"

    if isinstance(ex, aiohttp.ClientConnectorError):
        return f"could not connect: {ex.os_error.strerror or ex.os_error}"

    return f"{type(ex).__name__}: {ex}"[:200]


async def certificate_expiry(
    url: str,
    timeout: float,
    ssl_context: ssl.SSLContext | None = None,
) -> float | None:
    """
    The certificate's expiry as a Unix time, or None for plain HTTP.

    A separate TLS connection is made for this: aiohttp may already
    have released the request's connection when the answer has no
    body. With verification on, getpeercert() returns the parsed
    certificate, including "notAfter".
    """
    parts = urlsplit(url)

    if parts.scheme != "https":
        return None

    context = ssl_context or ssl.create_default_context()

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            parts.hostname,
            parts.port or 443,
            ssl=context,
            server_hostname=parts.hostname,
        ),
        timeout=timeout,
    )

    try:
        certificate = writer.get_extra_info("peercert") or {}
    finally:
        writer.close()

        try:
            await writer.wait_closed()
        except Exception:
            pass

    expires = certificate.get("notAfter")
    return ssl.cert_time_to_seconds(expires) if expires else None


async def check(
    url: str,
    timeout: float,
    ssl_context: ssl.SSLContext | None = None,
) -> dict:
    """
    Request one URL. Returns:
        up        True when it answered with a status below 500
        status    HTTP status, or None without an answer
        reason    why it counts as down ("" when up)
        expires   certificate expiry (Unix time) for https, else None
    """
    result = {"up": False, "status": None, "reason": "", "expires": None}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as session:
            # Redirects are not followed: a redirect to a login page on
            # another host already shows that the service answers.
            async with session.get(
                url,
                allow_redirects=False,
                ssl=ssl_context if ssl_context is not None else True,
            ) as response:
                result["status"] = response.status
    except Exception as ex:
        result["reason"] = _reason(ex)
        return result

    if result["status"] >= 500:
        result["reason"] = f"HTTP {result['status']}"
        return result

    result["up"] = True

    try:
        result["expires"] = await certificate_expiry(url, timeout, ssl_context)
    except Exception:
        # The request itself just succeeded over TLS, so this is a
        # passing problem; the certificate is read again next time.
        result["expires"] = None

    return result
