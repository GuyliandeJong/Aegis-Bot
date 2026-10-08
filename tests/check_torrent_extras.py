"""Watchlist (button, store, twice-daily check, notification), /torrent cleanup, and the free-space warning after adding."""
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
for key in [k for k in os.environ if k.startswith("JELLYFIN_")]:
    del os.environ[key]
os.environ.update({"SEED_RATIO_TARGET": "2", "SEED_DAYS_TARGET": "14"})

import discord
from services import watchlist as W
import services.qbittorrent_service as QS
import commands.torrent.search as SM
import commands.torrent.add as AM
import commands.torrent.cleanup as CL
from commands.torrent.info import name_matches
import commands.torrent.watchlist as WV
import monitors.watchlist_monitor as WM
import isolate  # state files go to a temporary folder
W.STATE_FILE = isolate.DATA / "watchlist.json"
WM.WatchlistMonitor.STATE_FILE = isolate.DATA / "watchlist_monitor.json"
WM.WatchlistMonitor.PAUSE = 0


class Message:
    def __init__(s, mid=1): s.id = mid; s.edits = []; s.flags = types.SimpleNamespace(ephemeral=False); s.channel = types.SimpleNamespace(id=10)
    async def edit(s, **k): s.edits.append(k)


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


class Inter:
    def __init__(s, uid=1, admin=False):
        s.log = []; s.message = Message(); s.response = Response(s); s.channel = types.SimpleNamespace(id=55)
        s.user = types.SimpleNamespace(id=uid, mention=f"<@{uid}>", guild_permissions=types.SimpleNamespace(administrator=admin))
        s.followup = types.SimpleNamespace(send=s._followup)
    async def _followup(s, text=None, **k): s.log.append(("followup", dict(k, text=text)))
    async def edit_original_response(s, **k):
        s.log.append(("edit_original", k))
        if isinstance(k.get("view"), discord.ui.View): k["view"].to_components()
        return s.message
    async def original_response(s): return s.message


def last(i): return i.log[-1][1]


async def watchlist_store():
    print("== watchlist store ==")
    print("add Dune      :", W.add("Dune  Part Two", "Movies", 1, 55))
    print("add again     :", W.add("dune part two", "Movies", 1, 55))
    print("other person  :", W.add("Dune Part Two", "Movies", 2, 55))
    W.MAX_PER_USER = 2
    W.add("Alien", "Movies", 1, 55)
    print("over the limit:", W.add("Heat", "Movies", 1, 55)[0])
    W.MAX_PER_USER = 25
    print("user 1 has    :", [e["query"] for e in W.for_user(1)])
    for e in W.for_user(1) + W.for_user(2): W.remove(e["id"])


class Cog(SM.TorrentSearchMixin, WV.TorrentWatchlistMixin):
    found = {}
    async def _check_jellyfin(self, query, category): return []
    async def _find_torrents(self, query, category):
        return (Cog.found.get(query, []), {}, query, False)


async def no_results_button():
    print("\n== a search that finds nothing ==")
    i = Inter(uid=1)
    await Cog()._search(i, query="Obscure Film 1971", category="Movies")
    k = last(i)
    print("message:", k["content"].splitlines()[-1], "| buttons:", [b.label for b in k["view"].children])
    other = Inter(uid=2); await k["view"].children[0].callback(other) if await k["view"].interaction_check(other) else None
    print("someone else presses:", last(other).get("text", "")[:60])
    press = Inter(uid=1); await k["view"].children[0].callback(press)
    print("owner presses       :", last(press)["content"][:70])
    print("watchlist           :", [(e["query"], e["category"], e["channel_id"]) for e in W.all_entries()])
    w = Inter(uid=1); await Cog()._watchlist(w)
    print("/torrent watchlist  :", last(w)["embed"].description.splitlines()[0][:40], "| ephemeral:", last(w).get("ephemeral"))


class Channel:
    def __init__(s, cid): s.id = cid; s.posts = []
    async def send(s, **k): s.posts.append(k); return Message(70)


async def monitor():
    print("\n== twice-daily check ==")
    W.add("Second Film", "PC", 3, 0)
    channels = {55: Channel(55), 10: Channel(10)}
    searched = []

    class Searcher(Cog):
        async def _find_torrents(self, query, category):
            searched.append(query)
            return await super()._find_torrents(query, category)

    bot = types.SimpleNamespace(get_channel=lambda cid: channels.get(cid), get_cog=lambda name: Searcher())
    mon = WM.WatchlistMonitor(types.SimpleNamespace(bot=bot))
    clock = {"t": 1_000_000.0}
    mon._now = lambda: clock["t"]
    await mon.check(); print("first check: searched", searched, "| posts:", len(channels[55].posts) + len(channels[10].posts))
    Cog.found["Obscure Film 1971"] = [{"title": "Obscure.Film.1971.1080p"}]
    clock["t"] += 3600
    searched.clear(); await mon.check(); print("1 hour later: searched", searched)
    clock["t"] += 12 * 3600
    searched.clear(); await mon.check(); print("12 hours later: searched", searched)
    post = channels[55].posts[-1]
    print("posted in the search channel:", post["content"], "|", post["embed"].title, "|", post["embed"].description.splitlines()[0])
    print("left on the watchlist:", [e["query"] for e in W.all_entries()])
    Cog.found["Second Film"] = [{"title": "Second.Film.x"}]
    clock["t"] += 12 * 3600; await mon.check()
    print("entry without a channel -> alert channel posts:", len(channels[10].posts), channels[10].posts[-1]["content"])

    view = post["view"]; view.to_components()
    called = []
    class Recorder:
        async def _search(self, interaction, query, category): called.append((query, category))
    view.bot = types.SimpleNamespace(get_cog=lambda name: Recorder())
    stranger = Inter(uid=9); ok = await view.interaction_check(stranger)
    print("stranger may press Show results:", ok)
    await view.children[0].callback(Inter(uid=1))
    print("owner presses Show results -> search:", called)
    for post in channels[10].posts: post["view"].stop()
    for e in W.all_entries(): W.remove(e["id"])


def torrent(h, name, ratio, days, progress=1.0, size=4 * 1024 ** 3):
    return {"hash": h, "name": name, "ratio": ratio, "seeding_time": int(days * 86400), "progress": progress, "size": size, "category": "Movies"}


async def cleanup():
    print("\n== /torrent cleanup ==")
    torrents = [torrent("a", "Low ratio new", 0.3, 1), torrent("b", "High ratio", 3.1, 2), torrent("c", "Long seeded", 0.8, 20),
                torrent("d", "Still downloading", 0, 0, progress=0.4)]
    deleted = []

    async def get_torrents(self, category=None, filter_name=None): return torrents
    async def delete(self, h, delete_files=False): deleted.append((h, delete_files))
    QS.QBittorrentService.get_torrents = get_torrents
    QS.QBittorrentService.delete_torrent = delete

    i = Inter(uid=5, admin=False); await Cog2()._cleanup(i)
    print("not an admin:", last(i)["text"])
    i = Inter(uid=5, admin=True); await Cog2()._cleanup(i)
    k = last(i); embed, view = k["embed"], k["view"]; view.to_components()
    print("summary:", embed.description.splitlines()[0])
    print("order  :", [o.label for o in view.select.options])
    print("ephemeral:", k["ephemeral"], "| buttons disabled before choosing:", [b.disabled for b in view.children[1:]])
    view.select._values = ["b", "c"]
    await view.select.callback(Inter(uid=5, admin=True))
    print("after choosing 2:", [b.disabled for b in view.children[1:]])
    ask = Inter(uid=5, admin=True); await view.files_button.callback(ask)
    confirm = last(ask)["view"]
    print("files button asks:", last(ask)["content"][:45], [b.label for b in confirm.children])
    back = Inter(uid=5, admin=True); await confirm.children[1].callback(back)
    print("back -> same picker:", last(back)["view"] is view, "| deleted so far:", deleted)
    ask = Inter(uid=5, admin=True); await view.files_button.callback(ask)
    done = Inter(uid=5, admin=True); await last(ask)["view"].children[0].callback(done)
    print("confirmed:", deleted, "|", done.log[-1][1]["content"])


class Cog2(CL.TorrentCleanupMixin):
    pass


async def cleanup_pages():
    print("\n== /torrent cleanup with many torrents ==")
    many = [torrent(f"h{n:02d}", f"Torrent {n:02d}", 3 - n / 20, 1) for n in range(60)]
    deleted = []

    async def get_torrents(self, category=None, filter_name=None): return many
    async def delete(self, h, delete_files=False): deleted.append(h)
    QS.QBittorrentService.get_torrents = get_torrents
    QS.QBittorrentService.delete_torrent = delete

    i = Inter(uid=5, admin=True); await Cog2()._cleanup(i)
    view = last(i)["view"]; view.to_components()
    print("first page:", last(i)["embed"].footer.text, "| options:", len(view.select.options), "| previous disabled:", view.previous_button.disabled)
    view.select._values = ["h00", "h01"]
    await view.select.callback(Inter(uid=5, admin=True))
    n = Inter(uid=5, admin=True); await view.next_button.callback(n)
    print("after Next:", last(n)["embed"].footer.text, "| first option:", view.select.options[0].label)
    view.select._values = ["h30"]
    await view.select.callback(Inter(uid=5, admin=True))
    n = Inter(uid=5, admin=True); await view.next_button.callback(n)
    print("last page:", last(n)["embed"].footer.text, "| options:", len(view.select.options), "| next disabled:", view.next_button.disabled)
    b = Inter(uid=5, admin=True); await view.previous_button.callback(b); await view.previous_button.callback(Inter(uid=5, admin=True))
    print("back on page 1, still ticked:", [o.label for o in view.select.options if o.default], "|", last(b)["embed"].description.splitlines()[1])
    await view.keep_button.callback(Inter(uid=5, admin=True))
    print("removed across pages:", deleted)


async def space():
    print("\n== free-space warning after adding ==")
    AM.TorrentAddMixin.SPACE_CHECK_INTERVAL = 0
    state = {"calls": 0, "free": 10 * 1024 ** 3}

    async def get_torrents(self, category=None, filter_name=None):
        state["calls"] += 1
        size = 0 if state["calls"] < 3 else 30 * 1024 ** 3
        return [{"hash": "abc", "name": "Big.Movie.2160p", "size": size, "amount_left": size}]

    async def free_space(self): return state["free"]
    QS.QBittorrentService.get_torrents = get_torrents
    QS.QBittorrentService.get_free_space = free_space

    class Adder(AM.TorrentAddMixin): pass
    i = Inter(uid=1); await Adder()._warn_if_no_space(i, "ABC", "Movies")
    print("size known after", state["calls"], "lookups ->", last(i)["text"][:95], "| ephemeral:", last(i).get("ephemeral"))
    state.update(calls=0, free=100 * 1024 ** 3)
    i = Inter(uid=1); await Adder()._warn_if_no_space(i, "ABC", "Movies")
    print("enough space -> messages:", len(i.log))


def info_matching():
    print("\n== /torrent info name matching ==")
    name = "The.Dark.Knight.Rises.2012.1080p.BluRay.DDP5.1.x265.10bit-GalaxyRG265"
    for query in ("Dark Knight", "dark knight rises 2012", "knig", "dark kight", "batman"):
        print(f"  {query!r:26} -> {name_matches(query, name)}")


async def main():
    info_matching()
    await watchlist_store()
    await no_results_button()
    await monitor()
    await cleanup()
    await cleanup_pages()
    await space()

asyncio.run(main())
