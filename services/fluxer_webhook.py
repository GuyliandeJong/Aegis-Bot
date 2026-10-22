"""
Mirror alerts to a Fluxer channel through a Fluxer webhook.

Discord stays the main channel: every alert the notification service
sends is also posted to Fluxer as one embed with the same title,
description, color and fields. Only alerts go there; the commands stay
on Discord until Fluxer supports interactions.

.env:
    FLUXER_WEBHOOK_URL   the webhook's full URL, as Fluxer shows it:
                         https://<api>/v1/webhooks/<id>/<token>
                         (empty: nothing is posted to Fluxer)

The payload follows Fluxer's docs (fluxerapp/fluxer,
fluxer_docs/src/content/docs/http-api/):
    webhooks.mdx  "Execute webhook": POST /v1/webhooks/{id}/{token}, a
                  JSON "Webhook message body", 204 without ?wait=true,
                  60 requests per minute per webhook.
    messages.mdx  "Rich embed input objects": title 256, description
                  4096, at most 25 fields, field name 256 and value
                  1024, footer text 2048 characters.

Discord-only markup is rewritten to plain text, because Fluxer's docs
do not describe it: <t:...> timestamps become "2026-10-19 04:00 UTC",
user and channel mentions become names, custom emoji their :name:.

Posting runs in the background with a short timeout, so it never
delays or breaks the Discord alert. Failures are logged, at most once
every few minutes, and never raised.
"""

import asyncio
import os
import re
import time
from datetime import datetime, timezone

import aiohttp

from utils.logger import logger


# Seconds one post to Fluxer may take, including one retry after a 429.
TIMEOUT = 10

# Longest wait for a rate limit before the alert is dropped.
MAX_RETRY_AFTER = 5

# Failures are logged at most once per this many seconds.
LOG_INTERVAL = 300

# Limits of Fluxer's rich embed input object (messages.mdx).
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_COUNT_LIMIT = 25
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FOOTER_LIMIT = 2048

_tasks: set[asyncio.Task] = set()
_lock: asyncio.Lock | None = None
_last_log = 0.0
_unlogged = 0


def webhook_url() -> str:
    return os.getenv("FLUXER_WEBHOOK_URL", "").strip()


def enabled() -> bool:
    return bool(webhook_url())


# <t:1760846400> or <t:1760846400:f>, Discord's timestamp markup.
TIMESTAMP = re.compile(r"<t:(-?\d+)(?::([tTdDfFR]))?>")

# <@123>, <@!123>, <@&123> and <#123>.
MENTION = re.compile(r"<(@!?|@&|#)(\d+)>")

# <:name:123> and <a:name:123>.
CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")


def _timestamp(match: re.Match) -> str:
    try:
        moment = datetime.fromtimestamp(int(match.group(1)), timezone.utc)
    except (OverflowError, OSError, ValueError):
        return match.group(0)

    style = match.group(2) or "f"

    if style in "dD":
        return moment.strftime("%Y-%m-%d")
    if style == "t":
        return moment.strftime("%H:%M UTC")
    if style == "T":
        return moment.strftime("%H:%M:%S UTC")
    if style == "F":
        return moment.strftime("%A %Y-%m-%d %H:%M UTC")

    # f, and R: a relative time would be wrong by the time it is read.
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def readable(text: str, bot=None) -> str:
    """
    Rewrite Discord-only markup as plain text.

    With the bot, mentions show the user's, role's or channel's name;
    without it, or when the name is unknown, a generic word.
    """
    if not text:
        return text

    def mention(match: re.Match) -> str:
        kind, number = match.group(1), int(match.group(2))
        name = None

        try:
            if bot is not None and kind.startswith("@") and kind != "@&":
                user = bot.get_user(number)
                name = user and (getattr(user, "display_name", None) or user.name)
            elif bot is not None and kind == "#":
                channel = bot.get_channel(number)
                name = channel and channel.name
            elif bot is not None:
                for guild in bot.guilds:
                    role = guild.get_role(number)
                    if role is not None:
                        name = role.name
                        break
        except Exception:
            name = None

        if kind == "#":
            return f"#{name or 'channel'}"

        return f"@{name or ('role' if kind == '@&' else 'user')}"

    text = TIMESTAMP.sub(_timestamp, text)
    text = MENTION.sub(mention, text)
    return CUSTOM_EMOJI.sub(r":\1:", text)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_payload(
    title: str,
    description: str,
    color,
    fields: list[tuple[str, str, bool]] | None = None,
    *,
    timestamp: datetime | None = None,
    bot=None,
) -> dict:
    """
    The webhook message body for one alert: a single rich embed.

    allowed_mentions is left out, which for a webhook suppresses every
    mention (webhooks.mdx, "Webhook message body", note 5).
    """
    embed: dict = {
        "color": int(getattr(color, "value", color) or 0),
        "footer": {"text": "Aegis-Bot"},
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }

    title = readable(title or "", bot).strip()
    description = readable(description or "", bot).strip()

    if title:
        embed["title"] = _cut(title, TITLE_LIMIT)

    # An empty description counts as absent, so it is left out.
    if description:
        embed["description"] = _cut(description, DESCRIPTION_LIMIT)

    if fields:
        embed["fields"] = [
            {
                # A field name must have at least one character.
                "name": _cut(readable(str(name), bot).strip() or "-", FIELD_NAME_LIMIT),
                "value": _cut(readable(str(value), bot).strip() or "-", FIELD_VALUE_LIMIT),
                "inline": bool(inline),
            }
            for name, value, inline in fields[:FIELD_COUNT_LIMIT]
        ]

    return {"embeds": [embed]}


def start(
    title: str,
    description: str,
    color,
    fields: list[tuple[str, str, bool]] | None = None,
    *,
    bot=None,
) -> bool:
    """
    Post the alert to Fluxer in the background when a webhook is set.

    Returns whether a post was scheduled; never blocks and never raises.
    """
    url = webhook_url()

    if not url:
        return False

    try:
        payload = build_payload(title, description, color, fields, bot=bot)
        task = asyncio.create_task(_post(url, payload, title))
    except Exception as ex:
        _log_failure(title, f"could not prepare the post: {type(ex).__name__}: {ex}")
        return False

    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def _post(url: str, payload: dict, title: str) -> None:
    global _lock

    # One post at a time keeps the alerts in order on Fluxer.
    if _lock is None:
        _lock = asyncio.Lock()

    try:
        async with _lock:
            await asyncio.wait_for(_send(url, payload, title), TIMEOUT)
    except asyncio.TimeoutError:
        _log_failure(title, f"no answer within {TIMEOUT} seconds")
    except Exception as ex:
        _log_failure(title, f"{type(ex).__name__}: {_redact(str(ex), url)}")


async def _send(url: str, payload: dict, title: str) -> None:
    async with aiohttp.ClientSession() as session:
        for attempt in range(2):
            async with session.post(url, json=payload) as response:
                if 200 <= response.status < 300:
                    logger.info("[Fluxer] Mirrored alert: %s", title)
                    return

                body = await response.text()
                wait = _retry_after(response, body)

                # One retry when Fluxer asks for a short pause.
                if (
                    response.status == 429
                    and attempt == 0
                    and wait is not None
                    and wait <= MAX_RETRY_AFTER
                ):
                    await asyncio.sleep(wait)
                    continue

                _log_failure(
                    title,
                    f"HTTP {response.status}: {_redact(body, url)[:200]}",
                )
                return


def _retry_after(response, body: str) -> float | None:
    value = response.headers.get("Retry-After")

    if value is None:
        match = re.search(r'"retry_after"\s*:\s*([\d.]+)', body or "")
        value = match and match.group(1)

    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def _redact(text: str, url: str) -> str:
    """The webhook URL contains its secret token; never log it."""
    token = url.rstrip("/").rsplit("/", 1)[-1]
    text = text.replace(url, "<webhook>")
    return text.replace(token, "<token>") if token else text


def _log_failure(title: str, reason: str) -> None:
    global _last_log, _unlogged

    now = time.monotonic()

    if _last_log and now - _last_log < LOG_INTERVAL:
        _unlogged += 1
        return

    extra = f" ({_unlogged} more failed since the last message)" if _unlogged else ""
    logger.warning(
        "[Fluxer] Could not mirror alert %r: %s%s",
        title,
        reason,
        extra,
    )
    _last_log = now
    _unlogged = 0
