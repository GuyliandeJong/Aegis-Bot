"""Quiet hours: the window (formats, crossing midnight, boundaries, unreadable values), which alerts are held, the summary when the window ends (by the next alert and by the monitor), restarts, maintenance taking precedence, and nothing posted when nothing was held."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
os.environ.pop("QUIET_HOURS", None)

import core.activity_log as AL
import core.maintenance as M
import core.quiet_hours as Q
from core.notification_service import NotificationService
from modules.general.quiet_hours_monitor import QuietHoursMonitor
import isolate  # state files go to a temporary folder

CLOCK = [datetime(2026, 11, 10, 12, 0)]
Q.now = lambda: CLOCK[0]


def at(hour, minute=0, day=10): CLOCK[0] = datetime(2026, 11, day, hour, minute)


class Channel:
    def __init__(self): self.posts = []
    async def send(self, content=None, embed=None, allowed_mentions=None, view=None):
        self.posts.append(embed); return embed


class Bot:
    def __init__(self, channel): self.channel = channel
    def get_channel(self, channel_id): return self.channel if channel_id == 10 else None


def titles(channel): return [e.title for e in channel.posts]


async def alerts(service):
    await service.warning(title="Datastore Filling Up", description="d")
    await service.warning(title="Datastore Filling Up", description="d")
    await service.recovered(title="Temperature Recovered", description="d")
    await service.info(title="New in Jellyfin", description="d")
    await service.success(title="✅ Torrent Completed", description="d")


async def main():
    channel = Channel()
    service = NotificationService(Bot(channel))
    monitor = QuietHoursMonitor(service)

    print("== the window ==")
    for value in ("23-7", "22:30-07:00", "7-23", " 1 - 5 ", "", "abc", "25-3", "5-5", "23:99-7", "23"):
        os.environ["QUIET_HOURS"] = value
        print(f"  {value!r:14} -> {Q.window()}")

    print("\n== the boundaries ==")
    os.environ["QUIET_HOURS"] = "23-7"
    print("  23-7 at 22:59 / 23:00 / 03:00 / 06:59 / 07:00 ->", [(at(h, mi), Q.active())[1] for h, mi in ((22, 59), (23, 0), (3, 0), (6, 59), (7, 0))])
    os.environ["QUIET_HOURS"] = "8-17"
    print("  8-17 at 07:59 / 08:00 / 16:59 / 17:00 / 23:00 ->", [(at(h, mi), Q.active())[1] for h, mi in ((7, 59), (8, 0), (16, 59), (17, 0), (23, 0))])
    os.environ["QUIET_HOURS"] = "22:30-07:00"
    print("  22:30-07:00 at 22:29 / 22:30 ->", [(at(h, mi), Q.active())[1] for h, mi in ((22, 29), (22, 30))])

    print("\n== off ==")
    os.environ.pop("QUIET_HOURS"); at(3)
    await alerts(service)
    print("  no setting at 03:00 -> posted:", len(channel.posts), "| held:", Q.pending())
    channel.posts.clear()

    print("\n== during the quiet hours ==")
    os.environ["QUIET_HOURS"] = "23-7"
    at(23, 30)
    before = len(AL.since(0, "alert"))
    await alerts(service)
    print("  posted:", titles(channel), "| held:", Q.pending())
    print("  activity log entries added:", len(AL.since(0, "alert")) - before)
    await service.critical(title="ESXi Not Reachable", description="d")
    await service.info(title="Weekly summary", description="d", exempt=True)
    print("  critical and a scheduled report go through:", titles(channel))
    at(2)
    await monitor.check()
    print("  the monitor inside the window -> posted in total:", len(channel.posts))

    print("\n== a restart of the bot ==")
    Q._state = None
    print("  held kept:", Q.pending(), "|", Q._load()["counts"])

    print("\n== the window ends ==")
    channel.posts.clear()
    at(7, 0, day=11)
    await monitor.check()
    print("  the monitor ->", titles(channel))
    summary = channel.posts[0]
    print("  text:", summary.description.split(":\n", 1)[0].split(" since")[0], "|", summary.description.split(":\n", 1)[1].replace("\n", " / "))
    print("  per level:", summary.fields[0].value)
    await monitor.check()
    print("  second check -> posted in total:", len(channel.posts), "| held:", Q.pending())

    print("\n== the next alert flushes first ==")
    at(1, 0, day=12)
    await service.warning(title="Held Again", description="d")
    channel.posts.clear()
    at(7, 5, day=12)
    await service.warning(title="Morning Alert", description="d")
    print("  ->", titles(channel))

    print("\n== nothing held ==")
    channel.posts.clear()
    at(8, 0, day=13)
    await monitor.check()
    print("  window over, nothing held -> posted:", len(channel.posts))

    print("\n== maintenance first ==")
    at(23, 30, day=13)
    M.start(60, "disk swap", "42")
    await service.warning(title="During Both", description="d")
    print("  held by quiet hours:", Q.pending(), "| muted by maintenance:", M.current()["counts"])
    M.end()

asyncio.run(main())
