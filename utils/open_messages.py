"""
Remembers which messages still show working buttons, so they can be
marked as expired after a restart.

Buttons are handled by View objects that live in memory. After a
restart nothing listens to them any more, and pressing one only gives
Discord's "This interaction failed". On startup the bot edits every
message still listed here: the buttons are removed and a note says to
run the command again.

Ephemeral messages (visible to one person only) cannot be edited after
a restart and are not recorded.
"""

import asyncio
import json

import discord

from config.paths import DATA_DIR
from utils.logger import logger


STATE_FILE = DATA_DIR / "open_messages.json"

# Older entries are dropped once the file holds more than this many.
MAX_ENTRIES = 200

RESTART_NOTE = (
    "The bot restarted, so these buttons no longer work. "
    "Run the command again to continue."
)

_open: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    global _open

    if _open is not None:
        return _open

    _open = {}

    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r") as file:
                data = json.load(file)

            if isinstance(data, dict):
                _open = {
                    str(message_id): entry
                    for message_id, entry in data.items()
                    if isinstance(entry, dict)
                }

        except Exception as ex:
            logger.error(
                "[OpenMessages] Failed to load %s: %s",
                STATE_FILE,
                ex,
            )

    return _open


def _save() -> None:
    temporary = STATE_FILE.with_suffix(".tmp")

    try:
        STATE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with temporary.open("w") as file:
            json.dump(
                _load(),
                file,
                indent=4,
            )

        temporary.replace(STATE_FILE)

    except Exception as ex:
        logger.error(
            "[OpenMessages] Failed to save %s: %s",
            STATE_FILE,
            ex,
        )


def remember(
    message,
    note: str = RESTART_NOTE,
    owner: str = "",
) -> bool:
    """
    Record a message whose buttons or progress depend on the bot.

    `owner` identifies who recorded it, so that a view finishing late
    cannot remove the record of a download that took over its message.
    Returns whether the message was recorded.
    """
    if message is None:
        return False

    flags = getattr(message, "flags", None)

    if flags is not None and getattr(flags, "ephemeral", False):
        return False

    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    message_id = getattr(message, "id", None)

    if not channel_id or not message_id:
        return False

    entries = _load()
    entries.pop(str(message_id), None)
    entries[str(message_id)] = {
        "channel_id": int(channel_id),
        "note": note,
        "owner": owner,
    }

    while len(entries) > MAX_ENTRIES:
        entries.pop(next(iter(entries)))

    _save()

    return True


def forget(message, owner: str = "") -> None:
    """Stop tracking a message, because it no longer needs the bot."""
    message_id = getattr(message, "id", None)

    if message_id is None:
        return

    entries = _load()
    entry = entries.get(str(message_id))

    if entry is None or entry.get("owner", "") != owner:
        return

    del entries[str(message_id)]
    _save()


async def expire_all(bot) -> None:
    """
    Edit every message left over from before the restart.

    Called once at startup. Messages that were deleted, or are in a
    channel the bot can no longer see, are skipped.
    """
    entries = dict(_load())

    if not entries:
        return

    expired = 0

    for message_id, entry in entries.items():
        try:
            channel = bot.get_channel(entry["channel_id"])

            if channel is None:
                channel = await bot.fetch_channel(entry["channel_id"])

            message = channel.get_partial_message(int(message_id))

            await message.edit(
                content=entry.get("note") or RESTART_NOTE,
                view=None,
            )
            expired += 1

        except (discord.NotFound, discord.Forbidden):
            pass

        except Exception as ex:
            logger.warning(
                "[OpenMessages] Could not expire message %s: %s",
                message_id,
                ex,
            )

    # Only the entries handled here: messages posted since startup
    # are still live.
    current = _load()

    for message_id, entry in entries.items():
        if current.get(message_id) is entry:
            del current[message_id]

    _save()

    logger.info(
        "[OpenMessages] Marked %d message(s) from before the restart "
        "as expired.",
        expired,
    )


class ExpiringView(discord.ui.View):
    """
    A View whose message is recorded until the view stops.

    Assigning `view.message` records the message; when the view times
    out or is stopped, the record is removed again. Anything still
    recorded at startup was cut off by a restart.

    A view not started by a command can set `restart_note` to say what
    to do instead of "run the command again".
    """

    _message = None

    restart_note = RESTART_NOTE

    @property
    def message(self):
        return self._message

    @message.setter
    def message(self, message) -> None:
        previous = self._message
        self._message = message

        if message is None or message is previous:
            return

        if previous is not None:
            forget(previous, self._owner)

        if not remember(message, note=self.restart_note, owner=self._owner):
            return

        try:
            asyncio.get_running_loop().create_task(
                self._forget_when_finished(message)
            )
        except RuntimeError:
            # No running event loop, for example in a check script.
            pass

    @property
    def _owner(self) -> str:
        return f"view:{id(self)}"

    async def _forget_when_finished(self, message) -> None:
        await self.wait()
        forget(message, self._owner)
