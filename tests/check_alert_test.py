"""/alert test: posts a test alert of the chosen level to the alert channel, administrators only by default, muted by maintenance except critical, and says why when nothing was posted."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime

from discord import app_commands

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c

import core.maintenance as M
import core.quiet_hours as Q
from modules.general.commands.alert import AlertCog, LEVELS
import isolate  # state files go to a temporary folder


class Channel:
    id = 10
    def __init__(self): self.posts = []
    async def send(self, content=None, embed=None, allowed_mentions=None, view=None):
        self.posts.append(embed)
        return types.SimpleNamespace(channel=self, embed=embed)


class Bot:
    def __init__(self, channel): self.channel = channel
    def get_channel(self, channel_id): return self.channel if channel_id == 10 else None


class Interaction:
    def __init__(self):
        self.user = types.SimpleNamespace(id=42)
        self.sent = []
        self.response = types.SimpleNamespace(defer=self._defer)
        self.followup = types.SimpleNamespace(send=self._send)
    async def _defer(self, **k): self.deferred = k
    async def _send(self, text, **k): self.sent.append((text, k.get("ephemeral")))


def choice(value): return next(c for c in LEVELS if c.value == value)


async def run(cog, level=None):
    i = Interaction()
    await cog.test.callback(cog, i, choice(level) if level else None)
    return i.sent[0]


async def main():
    channel = Channel()
    cog = AlertCog(Bot(channel))

    print("== the command ==")
    print("  administrators only by default:", AlertCog.alert_group.default_permissions.administrator)
    print("  levels offered:", [c.value for c in LEVELS])

    print("\n== posting ==")
    text, private = await run(cog)
    print("  default level ->", channel.posts[-1].title, "|", text, "| private:", private)
    for level in ("warning", "critical", "recovered", "success"):
        text, _ = await run(cog, level)
        print(f"  {level:9} ->", channel.posts[-1].title)
    print("  description:", channel.posts[-1].description)

    print("\n== during maintenance ==")
    M.start(30, "test", "42")
    before = len(channel.posts)
    text, _ = await run(cog, "warning")
    print("  warning muted -> posted:", len(channel.posts) - before, "|", text[:58])
    text, _ = await run(cog, "critical")
    print("  critical goes through ->", channel.posts[-1].title, "|", text)
    M.end()

    print("\n== during quiet hours ==")
    os.environ["QUIET_HOURS"] = "23-7"
    Q.now = lambda: datetime(2026, 11, 10, 2, 0)
    before = len(channel.posts)
    text, _ = await run(cog, "warning")
    print("  warning held -> posted:", len(channel.posts) - before, "|", text)
    text, _ = await run(cog, "critical")
    print("  critical goes through ->", channel.posts[-1].title, "|", text)
    Q.now = lambda: datetime(2026, 11, 10, 12, 0)
    os.environ.pop("QUIET_HOURS")
    Q.take()

    print("\n== no alert channel ==")
    lonely = AlertCog(Bot(None))
    i = Interaction()
    await lonely.test.callback(lonely, i, None)
    print("  ", i.sent[0][0][:70])

asyncio.run(main())
