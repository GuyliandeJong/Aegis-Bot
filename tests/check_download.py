"""Jellyfin download: service behaviour and the Discord flow, against a fake server."""
import asyncio
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import stubs

ROOT = Path(tempfile.mkdtemp(prefix="aegis-dl-"))
MOVIES, SERIES = ROOT / "Movies", ROOT / "Series"
os.environ.update({
    "JELLYFIN_1_URL": "http://home", "JELLYFIN_1_API_KEY": "k1", "JELLYFIN_1_NAME": "Home",
    "JELLYFIN_2_URL": "http://friend", "JELLYFIN_2_API_KEY": "k2", "JELLYFIN_2_NAME": "Friend",
    "JELLYFIN_MOVIES_DIR": str(MOVIES), "JELLYFIN_SERIES_DIR": str(SERIES),
})

FILES = {  # item id -> (reported path on the friend's server, content)
    "m1": (r"D:\Media\Movies\Arrival (2016)\Arrival.2016.1080p.mkv", b"A" * 5_000_000),
    "evil": ("/srv/../../../etc/..\\..\\passwd.mkv", b"x" * 10),
    "e1": ("/tv/Show/Season 02/Show.S02E01.mkv", b"1" * 300_000),
    "e2": ("/tv/Show/Season 02/Show.S02E02.mkv", b"2" * 300_000),
    "e3": ("/tv/Show/Season 02/Show.S02E03.mkv", b"3" * 300_000),
}
BEHAVIOUR = {"status": 200, "truncate": False, "length": None, "no_name": False, "fail_item": None, "delay": 0.0}
LOG = []


class Content:
    def __init__(s, data): s.data = data
    async def iter_chunked(s, n):
        data = s.data[: len(s.data) // 2] if BEHAVIOUR["truncate"] else s.data
        for i in range(0, len(data), 1_000_000):
            await asyncio.sleep(BEHAVIOUR["delay"]); yield data[i:i + 1_000_000]


class Ctx:
    def __init__(s, status, payload=None, headers=None, data=b""):
        s.r = types.SimpleNamespace(status=status, headers=headers or {}, content=Content(data))
        async def js(): return payload
        async def text(): return "body"
        s.r.json = js; s.r.text = text
    async def __aenter__(s): return s.r
    async def __aexit__(s, *a): return False


class FakeSession:
    def __init__(s, **k): s.closed = False
    async def close(s): s.closed = True
    def post(s, url, **k): LOG.append("POST " + url); return Ctx(204)
    def get(s, url, **k):
        LOG.append("GET " + url)
        if url.endswith("/Download"):
            item = url.split("/")[-2]
            status = BEHAVIOUR["status"]
            if BEHAVIOUR["fail_item"] == item: status = 500
            if status != 200: return Ctx(status)
            path, data = FILES[item]
            headers = {"Content-Length": str(BEHAVIOUR["length"] or len(data))}
            if BEHAVIOUR["no_name"]: headers["Content-Disposition"] = "attachment; filename*=UTF-8''From%20Header.mkv"
            return Ctx(200, headers=headers, data=data)
        if "/Seasons" in url:
            return Ctx(200, {"Items": [{"Id": "s1", "Name": "Season 1", "IndexNumber": 1}, {"Id": "s2", "Name": "Season 2", "IndexNumber": 2}]})
        if "/Episodes" in url:
            assert k["params"]["SeasonId"] == "s2"
            return Ctx(200, {"Items": [{"Id": e, "Path": FILES[e][0], "Name": e} for e in ("e1", "e2", "e3")]})
        term = k["params"]["SearchTerm"].lower()
        lib = {"http://home": [("Dune", 2021, "h1", "/m/Dune.mkv")],
               "http://friend": [("Dune", 2021, "f1", "/m/Dune.mkv"), ("Arrival", 2016, "m1", FILES["m1"][0]), ("Show", 2019, "show", "/tv/Show")]}
        base = url.rsplit("/Items", 1)[0]
        return Ctx(200, {"Items": [{"Name": n, "ProductionYear": y, "Id": i, "Path": p} for n, y, i, p in lib[base] if term in n.lower()]})


sys.modules["aiohttp"].ClientSession = FakeSession
from services.jellyfin_service import JellyfinService as J, JellyfinDownloadError


def tree():
    return sorted(str(p.relative_to(ROOT)).replace(os.sep, "/") for p in ROOT.rglob("*") if p.is_file())


async def dl(item, directory, filename="", **beh):
    BEHAVIOUR.update(status=200, truncate=False, length=None, no_name=False, fail_item=None, delay=0.0); BEHAVIOUR.update(beh)
    s = J()
    try:
        path, done = await s.download_item("Friend", item, directory, filename)
        return f"ok downloaded={done} -> {path.name} ({path.stat().st_size} bytes)"
    except JellyfinDownloadError as ex:
        return "ERROR: " + str(ex).replace(str(ROOT), "<lib>")
    finally:
        await s.close()

print("== service ==")
d = MOVIES / "Arrival (2016)"
name = J.media_filename({"Path": FILES["m1"][0]})
print("filename from Windows path :", name)
print("download                   :", asyncio.run(dl("m1", d, name)))
print("again (already there)      :", asyncio.run(dl("m1", d, name)))
print("hostile path -> filename   :", repr(J.media_filename({"Path": FILES["evil"][0]})))
print("refused (403)              :", asyncio.run(dl("m1", MOVIES / "X", "x.mkv", status=403)))
print("cut off halfway            :", asyncio.run(dl("m1", MOVIES / "Y", "y.mkv", truncate=True)))
print("no space (huge length)     :", asyncio.run(dl("m1", MOVIES / "Z", "z.mkv", length=10**18)))
print("name from header           :", asyncio.run(dl("m1", MOVIES / "H", "", no_name=True)))
blocker = ROOT / "blocked"; blocker.write_text("a file where a folder should go")
print("cannot create folder       :", asyncio.run(dl("m1", blocker / "sub", "b.mkv")))
blocker.unlink()
print("files on disk              :", tree())
print("leftover .part files       :", [f for f in tree() if f.endswith(".part")])

# ---------------- Discord flow
print("\n== discord flow ==")
import commands.torrent.search as SM
import commands.torrent.jellyfin_download as JD
JD.JellyfinTransfer.UPDATE_INTERVAL = 0.05


class Message:
    def __init__(s): s.id = 1; s.edits = []; s.sent = []; s.channel = types.SimpleNamespace(send=s._send, fetch_message=s._fetch)
    async def edit(s, **k): s.edits.append(k)
    async def _send(s, text, **k): s.sent.append(text)
    async def _fetch(s, mid): return s


class Response:
    def __init__(s, owner): s.owner = owner; s.done = False
    def is_done(s): return s.done
    async def _r(s, kind, **k):
        assert not s.done, "responded twice"; s.done = True; s.owner.log.append((kind, k))
    async def defer(s, **k): await s._r("defer")
    async def edit_message(s, **k): await s._r("edit_message", **k)
    async def send_message(s, text=None, **k): await s._r("send_message", text=text)


class Inter:
    def __init__(s, message, uid=1):
        s.log = []; s.message = message; s.response = Response(s); s.channel = message.channel
        s.user = types.SimpleNamespace(id=uid, mention="<@1>")
    async def edit_original_response(s, **k): assert s.response.done; s.log.append(("edit_original", k))
    async def original_response(s): return s.message


class Cog(SM.TorrentSearchMixin):
    pass


cog = Cog()


async def search(query, category):
    msg = Message(); i = Inter(msg)
    await cog._search(i, query=query, category=category)
    kind, k = i.log[-1]
    return msg, k["embed"], k["view"]


async def wait_idle():
    while JD._active_transfer is not None: await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)


async def scenario():
    # 1. in both libraries -> no download offered
    msg, embed, view = await search("Dune", "Movies")
    print(f"Dune (in both)      : [{embed.title}] view={type(view).__name__}")

    # 2. only on the friend's server -> download offered
    shutil.rmtree(MOVIES, ignore_errors=True)
    msg, embed, view = await search("Arrival", "Movies")
    print(f"Arrival (friend)    : [{embed.title}] view={type(view).__name__} buttons={[getattr(c, 'label', type(c).__name__) for c in view.children]}")
    print("                      " + embed.description.split(chr(10))[-1])
    BEHAVIOUR.update(delay=0.04)
    i = Inter(msg); await view._download(i)
    busy = Inter(Message()); await JD._reject_if_busy(busy)
    print("  second download while running ->", busy.log[-1][1]["text"][:60])
    await wait_idle()
    print("  progress updates:", sum(1 for e in msg.edits if e["embed"].title.startswith("Downloading")), "| final:", msg.edits[-1]["embed"].title)
    print("  " + " / ".join(l for l in msg.edits[-1]["embed"].description.split(chr(10)) if l))
    print("  mention:", msg.sent, "| library refresh requested on:", [l for l in LOG if l.startswith("POST")][-1])
    BEHAVIOUR.update(delay=0.0)

    # 3. series -> season picker -> only that season
    msg, embed, view = await search("Show", "Series")
    i = Inter(msg); await view._download(i)
    season_view = i.log[-1][1]["view"]
    print(f"Show (series)       : picker options={[o.label for o in season_view.select.options]}")
    season_view.select.values = ["s2"]
    i2 = Inter(msg); await season_view._choose(i2); await wait_idle()
    print("  final:", msg.edits[-1]["embed"].title, "|", [l for l in msg.edits[-1]["embed"].description.split(chr(10)) if "Copied" in l])

    # 4. one episode fails mid-season, then retry skips what is already there
    shutil.rmtree(SERIES, ignore_errors=True)
    msg, embed, view = await search("Show", "Series")
    i = Inter(msg); await view._download(i); sv = i.log[-1][1]["view"]; sv.select.values = ["s2"]
    BEHAVIOUR.update(fail_item="e2")
    await sv._choose(Inter(msg)); await wait_idle()
    print("Episode 2 fails     :", msg.edits[-1]["embed"].title, "|", " / ".join(l for l in msg.edits[-1]["embed"].description.split(chr(10))[2:] if l))
    BEHAVIOUR.update(fail_item=None)
    msg, embed, view = await search("Show", "Series")
    i = Inter(msg); await view._download(i); sv = i.log[-1][1]["view"]; sv.select.values = ["s2"]
    await sv._choose(Inter(msg)); await wait_idle()
    print("Retry               :", msg.edits[-1]["embed"].title, "|", " / ".join(l for l in msg.edits[-1]["embed"].description.split(chr(10))[2:4] if l))

    # 5. folder not configured -> plain message, no download button
    del os.environ["JELLYFIN_MOVIES_DIR"]
    msg, embed, view = await search("Arrival", "Movies")
    print(f"No folder configured: [{embed.title}] view={type(view).__name__}")


# SelectOption / Button need real attributes for this test
class Opt:
    def __init__(s, label, value): s.label = label; s.value = value


class Sel:
    def __init__(s, **k): s.options = k["options"]; s.values = []


class Btn:
    def __init__(s, **k): s.label = k.get("label")


for m in (sys.modules["discord"], ):
    m.SelectOption = Opt
    m.ui.Select = Sel
    m.ui.Button = Btn
SM.TorrentJellyfinSearchButton.__bases__ = (Btn,)
asyncio.run(scenario())
print("\nfiles on disk:", tree())
shutil.rmtree(ROOT, ignore_errors=True)
