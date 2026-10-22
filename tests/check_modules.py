"""The modules: every module.py is valid and everything it names loads, which modules run for a given .env and MODULES, the startup report names missing settings but never values, the weekly summary keeps only fields of running modules, and bot.py refuses to start without the Discord values."""
import asyncio
import logging
import os
import sys
import types

LOGS = []


class Capture(logging.Handler):
    def emit(self, record):
        LOGS.append((record.levelname, record.getMessage()))


logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aegis"); log.setLevel(logging.INFO); log.addHandler(Capture()); log.propagate = False
m = types.ModuleType("utils.logger"); m.logger = log; sys.modules["utils.logger"] = m

SECRET = "hunter2-secret-value"
EVERYTHING = {
    "IDRAC_HOST": "idrac.test", "IDRAC_USERNAME": "u", "IDRAC_PASSWORD": SECRET,
    "ESXI_HOST": "esxi.test", "ESXI_USERNAME": "u", "ESXI_PASSWORD": SECRET,
    "PORTAINER_URL": "http://portainer.test", "PORTAINER_API_KEY": SECRET, "PORTAINER_ENDPOINT_ID": "1",
    "GPTWOL_HOST": "wol.test", "GPTWOL_TARGET_IP": "192.0.2.10", "GPTWOL_TARGET_MAC": "00:00:5e:00:53:01",
    "QBITTORRENT_HOST": "qbt.test", "QBITTORRENT_USERNAME": "u", "QBITTORRENT_PASSWORD": SECRET,
    "JELLYFIN_1_URL": "http://jellyfin.test", "JELLYFIN_1_API_KEY": SECRET,
    "BACKUP_PATHS": "/srv", "MINECRAFT_SERVERS": "Test=mc.test", "UPTIME_URLS": "https://example.test",
    "FLUXER_WEBHOOK_URL": "https://fluxer.test/v1/webhooks/1/" + SECRET,
}
for key in list(os.environ):
    if key in EVERYTHING or key.split("_")[0] in {"JELLYFIN", "DISCORD", "GUILD", "ALERT", "MODULES"}:
        del os.environ[key]

from core import modules as MD
import isolate  # state files go to a temporary folder

found = MD.discover()
names = [mod.name for mod in found]
print("modules:", names)


def summary(states):
    return " ".join(f"{s.module.name}={'on' if s.enabled else 'off'}" for s in states)


print("\n== every module.py ==")
problems = []
for mod in found:
    for target in mod.monitors:
        if not isinstance(MD.resolve(target), type):
            problems.append(target)
    for target in filter(None, (mod.setup, mod.startup_checks, mod.shutdown)):
        if not callable(MD.resolve(target)):
            problems.append(target)
    for extension in mod.commands():
        if not callable(getattr(__import__(extension, fromlist=["setup"]), "setup", None)):
            problems.append(extension)
print("  monitors, setups, checks and commands that do not load:", problems)
print("  always on:", [mod.name for mod in found if mod.always_on])

print("\n== which modules run ==")
print("  nothing set, auto:", summary(MD.statuses(found, "auto")))
os.environ.update(EVERYTHING)
print("  everything set, auto:", summary(MD.statuses(found, "auto")))
print("  MODULES=torrents,jellyfin:", summary(MD.statuses(found, "torrents,jellyfin")))
print("  MODULES=auto,-wan,-host:", [s.module.name for s in MD.statuses(found, "auto,-wan,-host") if not s.enabled])
LOGS.clear()
MD.statuses(found, "auto,minecraf")
print("  unknown name:", LOGS)

print("\n== startup report ==")
del os.environ["QBITTORRENT_PASSWORD"]; del os.environ["MINECRAFT_SERVERS"]
os.environ["MODULES"] = "auto,torrents"
LOGS.clear()
states = MD.statuses(found)
MD.report(states)
for level, line in LOGS:
    if "torrents" in line or "minecraft" in line or "general" in line:
        print(f"  {level:7} |{line}")
print("  any value in the log:", any(SECRET in line or "qbt.test" in line for _, line in LOGS))
os.environ.update(EVERYTHING); del os.environ["MODULES"]

print("\n== enabled() ==")
print("  before activate, any name:", MD.enabled("minecraft"))
MD.activate(MD.statuses(found, "general,host"))
print("  after activate:", [n for n in names if MD.enabled(n)])


async def summary_fields():
    import modules.general.weekly_summary_monitor as WS

    WS.WeeklySummaryMonitor.STATE_FILE = isolate.DATA / "weekly.json"
    monitor = WS.WeeklySummaryMonitor(None)

    async def disks(since=None):
        return "disks"

    monitor._disks = disks
    print("  general and host only:", [name for name, _, _ in await monitor.build(0)])
    print("  every field belongs to a module:", sorted({mod for mod, _, _ in WS.WeeklySummaryMonitor.FIELDS} - set(names)) == [])


print("\n== weekly summary ==")
asyncio.run(summary_fields())


async def everything_loads():
    from discord.ext import commands
    import discord

    MD.activate(MD.statuses(found, "auto"))
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    failed = []
    for mod in found:
        for extension in mod.commands():
            try:
                await bot.load_extension(extension)
            except Exception as ex:
                failed.append((extension, repr(ex)[:80]))
    print("  extensions that failed to load:", failed)
    print("  slash commands:", sorted(c.name for c in bot.tree.get_commands()))


print("\n== loading every command ==")
asyncio.run(everything_loads())

print("\n== bot.py without the Discord values ==")
import subprocess
from pathlib import Path

env = {k: v for k, v in os.environ.items() if k not in {"DISCORD_TOKEN", "GUILD_ID", "ALERT_CHANNEL_ID"}}
env["GUILD_ID"] = "not-a-number"
root = Path(__file__).resolve().parent.parent
result = subprocess.run([sys.executable, "-c", (
    "import logging, sys, types; m = types.ModuleType('utils.logger'); "
    "logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stdout); "
    "m.logger = logging.getLogger('aegis'); sys.modules['utils.logger'] = m; "
    "import asyncio, bot; asyncio.run(bot.main())"
)], cwd=root, env=env, capture_output=True, text=True, timeout=60)
print("  exit code:", result.returncode)
print("  says:", [line for line in result.stdout.splitlines() if "Missing" in line])
