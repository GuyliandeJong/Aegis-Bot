"""Maintenance mode in NotificationService against a fake alert channel: muting, critical alerts, restart, expiry and the end message."""
import asyncio
import json
import logging
import sys
import time
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c

import services.activity_log as AL
import services.maintenance as M
from monitors.maintenance_monitor import MaintenanceMonitor
from services.notification_service import NotificationService
import isolate  # state files go to a temporary folder


class Channel:
    def __init__(self): self.posts = []
    async def send(self, content=None, embed=None, allowed_mentions=None, view=None):
        self.posts.append(embed)
        return embed


class Bot:
    def __init__(self, channel): self.channel = channel
    def get_channel(self, channel_id): return self.channel if channel_id == 10 else None


def titles(channel): return [e.title for e in channel.posts]


def restart():
    """What a bot restart does: module state is gone, the file stays."""
    M._state = None


async def alerts(service):
    await service.warning(title="Datastore Filling Up", description="d")
    await service.warning(title="Datastore Filling Up", description="d")
    await service.recovered(title="Temperature Recovered", description="d")
    await service.info(title="New in Jellyfin", description="d")
    await service.success(title="Now in Jellyfin", description="d")
    await service.critical(title="ESXi Not Reachable", description="d")


async def main():
    print("state file isolated:", str(M.STATE_FILE).startswith(str(isolate.DATA)))
    channel = Channel()
    service = NotificationService(Bot(channel))

    await service.warning(title="Before", description="d")
    print("before maintenance posted:", titles(channel))

    channel.posts.clear()
    await service.start_maintenance(60, "Replacing a disk", 42)
    start = channel.posts[0]
    print("start message:", start.title, "|", "<@42>" in start.description, "<t:" in start.description, "Replacing a disk" in start.description)

    channel.posts.clear()
    before = len(AL.since(0, "alert"))
    await alerts(service)
    print("posted during maintenance:", titles(channel))
    print("activity log entries added:", len(AL.since(0, "alert")) - before)
    print("logged:", [(e["level"], e["title"]) for e in AL.since(0, "alert")][before:])
    print("counts:", sorted(M.current()["counts"].items()))

    # Scheduled reports are not alerts and still go out.
    await service.info(title="Weekly summary", description="d", exempt=True)
    print("exempt info posted:", titles(channel)[-1])

    restart()
    print("after restart active:", M.active(), "| reason:", M.current()["reason"], "| muted kept:", len(M.current()["muted"]))
    channel.posts.clear()
    await service.warning(title="Datastore Filling Up", description="d")
    print("muted after restart:", titles(channel), "| warning count:", M.current()["counts"]["warning"])

    # Run out: the monitor posts the end message with no alert arriving.
    M._load()["until"] = int(time.time()) - 1
    M._save()
    restart()
    await MaintenanceMonitor(service).check()
    end = channel.posts[-1]
    print("end message:", end.title)
    print(end.description)
    print("fields:", [(f.name, f.value) for f in end.fields][1:])
    print("file after end:", json.loads(M.STATE_FILE.read_text()), "| active:", M.active())
    await MaintenanceMonitor(service).check()
    print("end messages after a second check:", len(channel.posts))

    channel.posts.clear()
    await service.warning(title="After", description="d")
    print("after maintenance posted:", titles(channel))

    # Lazy expiry: the next alert ends maintenance first, then posts.
    channel.posts.clear()
    await service.start_maintenance(30, "", 42)
    await service.recovered(title="VM Running", description="d")
    M._load()["until"] = int(time.time()) - 1
    await service.warning(title="VM Stopped", description="d")
    print("lazy expiry:", titles(channel), "|", channel.posts[1].description.splitlines()[0])

    # Ending early, by command.
    channel.posts.clear()
    await service.start_maintenance(30, "", 42)
    await service.start_maintenance(120, "Longer", 43)
    print("start again:", titles(channel), "| reason:", M.current()["reason"])
    channel.posts.clear()
    await service.end_maintenance(43)
    print("ended early:", channel.posts[0].description, "|", channel.posts[0].fields[0].value.split(" · ")[1])
    print("end with none active:", await service.end_maintenance(43))

    # The kept titles are capped; the count is not.
    await service.start_maintenance(30, "", 42)
    M.MAX_MUTED = 3
    for n in range(5):
        await service.warning(title=f"Alert {n}", description="d")
    channel.posts.clear()
    await service.end_maintenance(42)
    print("capped:", channel.posts[0].description.replace("\n", " / "))


asyncio.run(main())
