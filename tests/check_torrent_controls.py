"""Admin approval (off by default, on with REQUEST_APPROVAL) and /torrent speed."""
import asyncio
import logging
import os
import sys
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, QBITTORRENT_HOST="h", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", DISCORD_USER_ID="")
sys.modules["config.settings"] = c
os.environ.pop("REQUEST_APPROVAL", None)

import discord
import modules.torrents.qbittorrent_service as QS
from modules.torrents import torrent_requesters as TR
from modules.torrents.commands.torrent import Torrent
import modules.torrents.torrent.add as AM
from modules.torrents.torrent.approval import magnet_name
import isolate  # state files go to a temporary folder
AM.TorrentAddMixin.SPACE_CHECK_TRIES = 1

MAGNET = "magnet:?xt=urn:btih:ABCDEF1234567890&dn=Arrival.2016.1080p.BluRay.x265"
ADDED, TOGGLES = [], []
STATE = {"slow": False}


async def add_torrent(self, magnet_url, category): ADDED.append(category); return "abcdef1234567890"
async def get_torrents(self, category=None, filter_name=None):
    return [{"hash": "abcdef1234567890", "name": "Arrival.2016.1080p", "category": "Movies", "state": "downloading", "progress": 0.1, "size": 1}]
async def free(self): return 10 ** 15
async def get_alt(self): return STATE["slow"]
async def request(self, method, endpoint, **k):
    if endpoint == "/transfer/toggleSpeedLimitsMode":
        TOGGLES.append(1); STATE["slow"] = not STATE["slow"]
async def prefs(self): return {"dl_limit": 0, "up_limit": 2 * 1024 ** 2, "alt_dl_limit": 5 * 1024 ** 2, "alt_up_limit": 1024 ** 2}
for name, fn in (("add_torrent", add_torrent), ("get_torrents", get_torrents), ("get_free_space", free),
                 ("get_alternative_speed", get_alt), ("_request", request), ("get_preferences", prefs)):
    setattr(QS.QBittorrentService, name, fn)


class Channel:
    id = 55
    def __init__(s): s.posts = []
    async def send(s, *a, **k): s.posts.append(k); return Message()


ALERTS = Channel()


class Message:
    id = 1; jump_url = "https://discord.com/channels/1/55/1"
    flags = types.SimpleNamespace(ephemeral=False); channel = types.SimpleNamespace(id=55)
    async def edit(s, **k): pass


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def _r(s, kind, **k):
        assert not s.done, "responded twice"; s.done = True; s.owner.log.append((kind, k))
        for v in k.values():
            if isinstance(v, discord.ui.View): v.to_components()
    async def defer(s, **k): await s._r("defer", **k)
    async def edit_message(s, **k): await s._r("edit_message", **k)
    async def send_message(s, text=None, **k): await s._r("send_message", text=text, **k)


def user(uid, admin):
    return types.SimpleNamespace(id=uid, mention=f"<@{uid}>", guild_permissions=types.SimpleNamespace(administrator=admin),
                                 display_name=f"user{uid}", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png"))


class Inter:
    def __init__(s, u):
        s.log = []; s.user = u; s.response = Response(s); s.channel = Channel()
        s.client = types.SimpleNamespace(get_channel=lambda cid: ALERTS)
        s.followup = types.SimpleNamespace(send=s._followup)
    async def _followup(s, text=None, **k): s.log.append(("followup", dict(k, text=text)))
    async def edit_original_response(s, **k):
        s.log.append(("edit_original", k))
        if isinstance(k.get("view"), discord.ui.View): k["view"].to_components()
        return Message()
    async def original_response(s): return Message()


def last(i): return i.log[-1][1]


async def approval():
    cog = Torrent(bot=None)
    print("== approval ==")
    print("magnet name:", magnet_name(MAGNET))

    i = Inter(user(2, admin=False)); await cog._add(i, category="Movies", magnet=MAGNET)
    print("setting off, non-admin adds -> qBittorrent adds:", len(ADDED), "|", last(i)["embed"].title)

    os.environ["REQUEST_APPROVAL"] = "true"
    ADDED.clear()
    i = Inter(user(3, admin=True)); await cog._add(i, category="Movies", magnet=MAGNET)
    print("setting on, admin adds      -> qBittorrent adds:", len(ADDED), "|", last(i)["embed"].title)

    ADDED.clear()
    i = Inter(user(4, admin=False)); await cog._add(i, category="Movies", magnet=MAGNET)
    k = last(i); view = k["view"]
    print("setting on, non-admin adds  -> qBittorrent adds:", len(ADDED), "|", k["embed"].title, "|", [b.label for b in view.children])
    print("  alert channel:", ALERTS.posts[-1]["embed"].title, "|", ALERTS.posts[-1]["embed"].description.splitlines()[-1])

    other = Inter(user(5, admin=False)); await view.children[0].callback(other)
    print("  non-admin presses Approve :", last(other)["text"], "| adds:", len(ADDED))
    other = Inter(user(5, admin=False)); await view.children[2].callback(other)
    print("  someone else cancels      :", last(other)["text"])

    admin = Inter(user(3, admin=True)); await view.children[0].callback(admin)
    print("  admin approves            -> adds:", len(ADDED), "|", last(admin)["embed"].title, "| requester recorded:", TR.mention_for("abcdef1234567890"), "|", last(admin)["embed"].footer.text, "| controls owner:", last(admin)["view"].owner_id)

    i = Inter(user(6, admin=False)); await cog._add(i, category="Series", magnet=MAGNET)
    view = last(i)["view"]
    admin = Inter(user(3, admin=True)); await view.children[1].callback(admin)
    print("  admin rejects             ->", last(admin)["embed"].title, "|", last(admin)["content"], "| adds:", len(ADDED))

    i = Inter(user(7, admin=False)); await cog._add(i, category="Movies", magnet=MAGNET)
    view = last(i)["view"]
    me = Inter(user(7, admin=False)); await view.children[2].callback(me)
    print("  requester cancels         ->", last(me)["embed"].title)
    os.environ.pop("REQUEST_APPROVAL")


async def speed():
    cog = Torrent(bot=None)
    print("\n== /torrent speed ==")
    i = Inter(user(2, admin=False)); await cog._speed(i, "slow")
    print("non-admin switches :", last(i)["text"][:50], "| toggles:", len(TOGGLES))
    i = Inter(user(2, admin=False)); await cog._speed(i, None)
    print("non-admin looks    :", last(i)["embed"].title, "|", last(i)["embed"].description.splitlines()[:2])
    i = Inter(user(3, admin=True)); await cog._speed(i, "slow")
    print("admin -> slow      :", last(i)["embed"].title, "|", last(i)["embed"].description.splitlines()[:2], "| toggles:", len(TOGGLES))
    i = Inter(user(3, admin=True)); await cog._speed(i, "slow")
    print("admin -> slow again: toggles:", len(TOGGLES))
    i = Inter(user(3, admin=True)); await cog._speed(i, "normal")
    print("admin -> normal    :", last(i)["embed"].title, "| toggles:", len(TOGGLES))


async def main():
    await approval()
    await speed()
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()

asyncio.run(main())
