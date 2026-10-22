"""The weekly "seeded enough" suggestion: schedule, list and overflow, who may press, removal keeping files, dismiss, expiry and restart."""
import asyncio
import json
import logging
import os
import sys
import types
from datetime import datetime

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18170
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, QBITTORRENT_HOST="127.0.0.1", QBITTORRENT_PORT=PORT, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", DISCORD_USER_ID="")
sys.modules["config.settings"] = c
# The real .env inside the container must not change what this expects.
for name in ("CLEANUP_REMINDER", "CLEANUP_REMINDER_DAY", "CLEANUP_REMINDER_HOUR", "SEED_RATIO_TARGET", "SEED_DAYS_TARGET"):
    os.environ.pop(name, None)

import discord
from modules.torrents.qbittorrent_service import QBittorrentService
from core.notification_service import NotificationService
from modules.torrents.seed_cleanup_monitor import SeedCleanupMonitor
from utils import open_messages as OM
import isolate  # state files go to a temporary folder
SeedCleanupMonitor.STATE_FILE = isolate.DATA / "seed_cleanup.json"

GIB = 1024 ** 3
DAY = 86400


def torrent(h, name, ratio, days, size, progress=1.0):
    return {"hash": h, "name": name, "ratio": ratio, "seeding_time": days * DAY, "size": size * GIB, "progress": progress}


TORRENTS = []
DELETES = []
LISTS = []


async def login(request): return web.Response(text="Ok.")
async def info(request): LISTS.append(1); return web.json_response(TORRENTS)
async def delete(request):
    form = await request.post()
    DELETES.append((form["hashes"], form["deleteFiles"]))
    TORRENTS[:] = [t for t in TORRENTS if t["hash"] != form["hashes"]]
    return web.Response(text="")


class Message:
    def __init__(s, channel, message_id, k):
        s.id = message_id; s.channel = channel; s.flags = types.SimpleNamespace(ephemeral=False); s.sent = k; s.edits = []
    async def edit(s, **k): s.edits.append(k)


class Channel:
    id = 10
    def __init__(s): s.posts = []; s.startup_edits = []
    async def send(s, **k):
        message = Message(s, 500 + len(s.posts), k); s.posts.append(message)
        if k.get("view") is not None: k["view"].to_components()
        return message
    def get_partial_message(s, message_id):
        channel = s
        class Partial:
            async def edit(p, **k): channel.startup_edits.append((message_id, k.get("content"), k.get("view")))
        return Partial()


CHANNEL = Channel()
BOT = types.SimpleNamespace(get_channel=lambda cid: CHANNEL if cid == 10 else None)


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def _r(s, kind, **k):
        assert not s.done, "responded twice"; s.done = True; s.owner.log.append((kind, k))
    async def edit_message(s, **k): await s._r("edit_message", **k)
    async def send_message(s, text=None, **k): await s._r("send_message", text=text, **k)


def user(uid, admin):
    return types.SimpleNamespace(id=uid, mention=f"<@{uid}>", guild_permissions=types.SimpleNamespace(administrator=admin),
                                 display_name=f"user{uid}", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png"))


class Inter:
    def __init__(s, u): s.log = []; s.user = u; s.response = Response(s)
    async def edit_original_response(s, **k): s.log.append(("edit_original", k))


async def press(view, label, who):
    """What discord.py does: interaction_check first, then the button's callback."""
    i = Inter(who)
    button = next(b for b in view.children if b.label == label)
    if await view.interaction_check(i):
        await button.callback(i)
    return i


def monitor_at(when):
    monitor = SeedCleanupMonitor(NotificationService(BOT))
    monitor._now = lambda: when
    return monitor


SATURDAY_NOON = datetime(2026, 10, 24, 12, 0)  # the default day and after 11:00


async def schedule():
    print("== schedule ==")
    TORRENTS[:] = [torrent("a1", "Arrival.2016.1080p", 2.5, 3, 40)]
    os.environ["CLEANUP_REMINDER"] = "false"
    await monitor_at(SATURDAY_NOON).check()
    print("turned off             -> posts:", len(CHANNEL.posts), "| qBittorrent asked:", len(LISTS))
    del os.environ["CLEANUP_REMINDER"]
    await monitor_at(datetime(2026, 10, 24, 10, 0)).check()
    print("saturday 10:00         -> posts:", len(CHANNEL.posts))
    await monitor_at(datetime(2026, 10, 23, 12, 0)).check()
    print("friday 12:00           -> posts:", len(CHANNEL.posts))
    TORRENTS[:] = [torrent("w1", "Waiting.2020", 0.5, 2, 10), torrent("d1", "Downloading.2021", 5.0, 0, 10, progress=0.5)]
    await monitor_at(SATURDAY_NOON).check()
    print("nothing seeded enough  -> posts:", len(CHANNEL.posts), "| qBittorrent asked:", len(LISTS))
    os.environ["CLEANUP_REMINDER_DAY"] = "monday"; os.environ["CLEANUP_REMINDER_HOUR"] = "8"
    print("monday 8 ->", SeedCleanupMonitor.schedule(), "| default ->", end=" ")
    del os.environ["CLEANUP_REMINDER_DAY"]; del os.environ["CLEANUP_REMINDER_HOUR"]
    print(SeedCleanupMonitor.schedule())


async def suggestion():
    print("== suggestion ==")
    SeedCleanupMonitor.STATE_FILE.unlink(missing_ok=True)
    TORRENTS[:] = [
        torrent("a1", "Arrival.2016.1080p", 2.5, 3, 40),
        torrent("b2", "Blade.Runner.2049.2017", 0.4, 20, 60),
        torrent("c3", "Castlevania.Belmonts.Curse", 3.1, 1, 20),
        torrent("w1", "Waiting.2020", 0.5, 2, 10),
        torrent("d1", "Downloading.2021", 5.0, 0, 10, progress=0.5),
    ]
    monitor = monitor_at(SATURDAY_NOON)
    await monitor.check()
    post = CHANNEL.posts[-1]; embed = post.sent["embed"]; view = post.sent["view"]
    print("posts:", len(CHANNEL.posts), "|", embed.title)
    print(embed.description.splitlines()[0])
    for line in embed.description.splitlines()[2:]:
        print(" ", line)
    print("buttons:", [b.label for b in view.children], "| timeout:", view.timeout, "| view.message set:", view.message is post)
    print("recorded for restart:", OM._load().get(str(post.id), {}).get("note"))
    await monitor_at(datetime(2026, 10, 24, 18, 0)).check()
    print("again that evening -> posts:", len(CHANNEL.posts))

    i = await press(view, "Remove these torrents (keep files)", user(2, admin=False))
    print("non-admin presses Remove ->", i.log, "| deletes:", DELETES)
    i = await press(view, "Dismiss", user(3, admin=False))
    print("non-admin presses Dismiss ->", i.log[0][1]["text"])

    TORRENTS[:] = [t for t in TORRENTS if t["hash"] != "b2"]  # removed by hand meanwhile
    i = await press(view, "Remove these torrents (keep files)", user(1, admin=True))
    print("admin presses Remove -> first:", i.log[0][1]["content"], "| view removed:", i.log[0][1]["view"] is None)
    print("  qBittorrent deletes:", DELETES)
    result = i.log[-1][1]["embed"]
    print("  message now:", result.title, "|", result.description.replace("\n", " / "))
    print("  left in qBittorrent:", sorted(t["hash"] for t in TORRENTS))
    await asyncio.sleep(0.05)
    print("  view finished:", view.is_finished(), "| record removed:", str(post.id) not in OM._load())


async def dismiss_and_overflow():
    print("== dismiss and overflow ==")
    SeedCleanupMonitor.STATE_FILE.unlink(missing_ok=True)
    DELETES.clear()
    TORRENTS[:] = [torrent(f"h{n:03d}", f"Some.Very.Long.Release.Name.Number.{n:03d}.2160p.WEB-DL.DDP5.1.Atmos.HDR.H.265-GROUP", 2.0 + n / 100, 30, 8)
                   for n in range(80)]
    await monitor_at(SATURDAY_NOON).check()
    post = CHANNEL.posts[-1]; embed = post.sent["embed"]; view = post.sent["view"]
    lines = embed.description.splitlines()
    print(lines[0])
    print("description length:", len(embed.description), "<= 4096:", len(embed.description) <= 4096,
          "| listed:", sum(1 for line in lines if line.startswith("•")), "|", lines[-1])
    i = await press(view, "Dismiss", user(1, admin=True))
    k = i.log[0][1]
    print("admin dismisses ->", k["embed"].title, "|", k["embed"].description, "| view removed:", k["view"] is None, "| deletes:", DELETES)
    return post


async def expiry_and_restart():
    print("== expiry and restart ==")
    SeedCleanupMonitor.STATE_FILE.unlink(missing_ok=True)
    TORRENTS[:] = [torrent("a1", "Arrival.2016.1080p", 2.5, 3, 40)]
    await monitor_at(SATURDAY_NOON).check()
    timed_out = CHANNEL.posts[-1]
    view = timed_out.sent["view"]
    view._dispatch_timeout()  # what discord.py does after two days
    await asyncio.sleep(0.05)
    print("after the timeout -> edit:", timed_out.edits, "| record removed:", str(timed_out.id) not in OM._load())

    SeedCleanupMonitor.STATE_FILE.unlink(missing_ok=True)
    await monitor_at(SATURDAY_NOON).check()
    cut_off = CHANNEL.posts[-1]
    print("saved to", OM.STATE_FILE.name, ":", json.loads(OM.STATE_FILE.read_text())[str(cut_off.id)]["note"])
    OM._open = None  # a new process reads the file again
    await OM.expire_all(BOT)
    for edit in CHANNEL.startup_edits:
        print("edited at startup:", edit)
    print("left after startup:", sorted(OM._load()))


async def main():
    app = web.Application()
    app.add_routes([web.post("/api/v2/auth/login", login), web.get("/api/v2/torrents/info", info),
                    web.post("/api/v2/torrents/delete", delete)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        await schedule()
        await suggestion()
        await dismiss_and_overflow()
        await expiry_and_restart()
    finally:
        await QBittorrentService.shutdown(); await runner.cleanup()

asyncio.run(main())
