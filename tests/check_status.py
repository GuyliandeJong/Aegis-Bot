"""/status: the monitors' health recorded by the monitor manager (success, failure, timeout, silence), the modules on and off with the reason, maintenance, the alerts of the last 24 hours, the title, and administrators only."""
import asyncio
import logging
import os
import re
import sys
import time
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0, MONITOR_TIMEOUT=1, MONITOR_INTERVAL=60)
sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith(("JELLYFIN_", "MODULES", "ESXI_", "IDRAC_", "GPTWOL_", "MINECRAFT_", "UPTIME_", "BACKUP_", "FLUXER_"))]:
    del os.environ[key]
os.environ["MODULES"] = "auto,-torrents"

import discord
from core import activity_log, maintenance, monitor_manager as MM
from modules.general.commands.status import StatusCog
import modules.general.commands.status as ST
import isolate  # state files go to a temporary folder


class Interaction:
    def __init__(self):
        self.user = types.SimpleNamespace(id=42, display_name="t", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png"))
        self.sent = []
        self.response = types.SimpleNamespace(defer=self._defer)
        self.followup = types.SimpleNamespace(send=self._send)
    async def _defer(self, **k): self.deferred = k
    async def _send(self, **k): self.sent.append(k)


async def status(cog):
    i = Interaction()
    await cog.status.callback(cog, i)
    embed = i.sent[0]["embed"]
    return embed, {f.name: f.value for f in embed.fields}, i.sent[0].get("ephemeral")


class Good:
    INTERVAL = 30
    async def check(self): pass


class Failing:
    INTERVAL = 30
    async def check(self): raise RuntimeError("Jellyfin said no")


class Slow:
    INTERVAL = 30
    async def check(self): await asyncio.sleep(5)


async def main():
    cog = StatusCog(types.SimpleNamespace(latency=0.042))

    print("== the command ==")
    print("  administrators only by default:", StatusCog.status.default_permissions.administrator)

    print("\n== monitors ==")
    MM.HEALTH.clear()
    embed, fields, private = await status(cog)
    print("  nothing has run ->", fields["Monitors"], "|", embed.title.split(" ", 1)[1], "| private:", private)

    manager = MM.MonitorManager(types.SimpleNamespace())
    for monitor in (Good(), Failing(), Slow()):
        await manager._run_monitor(monitor)
    seen, problems = ST.monitor_problems()
    print("  after one run each -> monitors seen:", seen)
    for line in problems:
        print("   ", re.sub(r"\d+s ago", "Ns ago", line.replace("**", "")))
    embed, fields, _ = await status(cog)
    print("  title:", embed.title.split(" ", 1)[1])

    MM.HEALTH.clear()
    await manager._run_monitor(Good())
    embed, fields, _ = await status(cog)
    print("  only a good one ->", fields["Monitors"], "|", embed.title.split(" ", 1)[1])
    MM.HEALTH["Good"]["last_ok"] = time.time() - 600
    print("  silent for 10 minutes (interval 30 s) ->", [l.replace("**", "") for l in ST.monitor_problems()[1]])
    MM.HEALTH["Good"]["last_ok"] = time.time() - 60
    print("  silent for 1 minute ->", ST.monitor_problems()[1])
    MM.HEALTH["Good"]["last_ok"] = time.time()
    MM.HEALTH["Failing"] = {"interval": 30, "last_ok": time.time(), "last_error": time.time() - 5}
    print("  an older failure after a later success ->", ST.monitor_problems()[1])

    print("\n== modules ==")
    on, off = ST.module_lines()
    print("  on:", on)
    print("  off:", off.splitlines())

    print("\n== maintenance ==")
    embed, fields, _ = await status(cog)
    print("  off ->", fields["Maintenance"])
    maintenance.start(30, "disk swap", "42")
    embed, fields, _ = await status(cog)
    print("  on  ->", fields["Maintenance"].split(":", 1)[0].split(" until")[0], "|", "disk swap" in fields["Maintenance"])
    maintenance.end()

    print("\n== alerts ==")
    activity_log._entries[:] = []
    print("  none ->", ST.alert_summary())
    for level, title in [("warning", "Datastore Filling Up"), ("warning", "Datastore Filling Up"), ("recovered", "Temperature Recovered"), ("critical", "ESXi Not Reachable")]:
        activity_log.record("alert", level=level, title=title)
    activity_log._entries.insert(0, {"time": int(time.time()) - 3 * 86400, "kind": "alert", "level": "critical", "title": "Old"})
    print("  summary ->", ST.alert_summary().replace("\n", " / "))
    embed, fields, _ = await status(cog)
    print("  field:", fields["Alerts, last 24 hours"].splitlines()[0])
    print("  bot field:", fields["Bot"].split(" · ")[1])

asyncio.run(main())
