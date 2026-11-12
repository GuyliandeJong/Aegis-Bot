"""Transcode alert against a fake Jellyfin: one warning per transcoded stream after two checks, who/what/why in the text, direct play and a short blip ignored, audio-only transcoding off by default, a server that does not answer changing nothing, another server ignored, and state across a restart."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("aegis").setLevel(logging.ERROR)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18195
for key in [k for k in os.environ if k.startswith(("JELLYFIN_", "SUBTITLE_"))]:
    del os.environ[key]
os.environ.update({"JELLYFIN_1_URL": f"http://127.0.0.1:{PORT}/home", "JELLYFIN_1_API_KEY": "h", "JELLYFIN_1_NAME": "Home",
                   "JELLYFIN_2_URL": f"http://127.0.0.1:{PORT}/friend", "JELLYFIN_2_API_KEY": "k", "JELLYFIN_2_NAME": "Friend"})

import modules.jellyfin.transcode_monitor as TMO
from modules.jellyfin.transcode import describe_reasons
import isolate  # state files go to a temporary folder

SESSIONS = {"home": [], "friend": []}
DOWN = set()
CALLS = []


def stream(method="Transcode", title="Night Drive", user="Alex", device="Living Room TV", video_direct=False,
           reasons=("VideoCodecNotSupported", "ContainerNotSupported"), height=1080, item=True):
    session = {"UserName": user, "Client": "Jellyfin Android", "DeviceName": device,
               "PlayState": {"PositionTicks": 60 * 10_000_000, "IsPaused": False, "PlayMethod": method}}
    if item:
        session["NowPlayingItem"] = {"Type": "Movie", "Name": title, "ProductionYear": 2024, "RunTimeTicks": 6000 * 10_000_000}
    if method == "Transcode":
        info = {"Height": height, "Bitrate": 4_000_000, "TranscodeReasons": list(reasons)}
        if video_direct is not None:
            info["IsVideoDirect"] = video_direct
            info["IsAudioDirect"] = False
        session["TranscodingInfo"] = info
    return session


def sessions_for(server):
    async def handler(request):
        CALLS.append(server)
        if server in DOWN:
            return web.Response(status=503)
        return web.json_response(SESSIONS[server])
    return handler


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


_files = [0]
CLOCK = [1_000_000.0]
TMO.TranscodeMonitor._now = staticmethod(lambda: CLOCK[0])


def warns(notes): return [s for s in notes.sent if s[0] == "warning"]
def stops(notes): return [s for s in notes.sent if s[0] == "recovered"]


def fresh():
    _files[0] += 1
    TMO.TranscodeMonitor.STATE_FILE = isolate.DATA / f"transcode-{_files[0]}.json"
    notes = Notes()
    return notes, TMO.TranscodeMonitor(notes)


async def run(mon, count=1):
    for _ in range(count):
        await mon.check()


async def main():
    app = web.Application()
    app.add_routes([web.get("/home/Sessions", sessions_for("home")), web.get("/friend/Sessions", sessions_for("friend"))])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== wording ==")
        print("  reasons:", describe_reasons(["ContainerNotSupported", "SubtitleCodecNotSupported", "HdrFancyNotSupported"]))

        print("\n== what is reported ==")
        notes, mon = fresh()
        SESSIONS["home"] = []
        await run(mon, 3)
        print("  nothing playing         ->", [t for _, t, _ in notes.sent])
        SESSIONS["home"] = [stream("DirectPlay"), stream("DirectStream", title="Other"), stream(item=False)]
        await run(mon, 3)
        print("  direct play and stream  ->", [t for _, t, _ in notes.sent])
        SESSIONS["home"] = [stream()]
        await run(mon)
        print("  first check transcoding ->", [t for _, t, _ in notes.sent])
        await run(mon)
        print("  second check            ->", [(lvl, t) for lvl, t, _ in notes.sent])
        print("  text:", notes.sent[0][2].replace("\n\n", " / ").replace("\n", " "))
        await run(mon, 4)
        print("  four more checks        -> alerts in total:", len(notes.sent))

        print("\n== a blip, and starting again ==")
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]; await run(mon)
        SESSIONS["home"] = [stream("DirectPlay")]; await run(mon, 3)
        SESSIONS["home"] = [stream()]; await run(mon)
        print("  one check, direct, one check ->", len(warns(notes)), "alert(s)")
        await run(mon)
        print("  two in a row again           ->", len(warns(notes)), "alert(s)")
        SESSIONS["home"] = []; await run(mon, 2)
        SESSIONS["home"] = [stream()]; await run(mon, 2)
        print("  stopped, then transcoding    ->", len(warns(notes)), "alert(s) in total")
        SESSIONS["home"] = [stream(), stream(user="Sam", title="Other Film", device="Phone")]; await run(mon, 2)
        print("  a second stream              ->", [s[2].split("**")[1] for s in warns(notes)[2:]])

        print("\n== when it stops ==")
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]
        await run(mon, 2)
        CLOCK[0] += 750
        SESSIONS["home"] = [stream("DirectPlay")]
        await run(mon)
        print("  one check of direct play  ->", len(stops(notes)), "message(s)")
        await run(mon)
        print("  second check              ->", [(lvl, t) for lvl, t, _ in stops(notes)])
        print("  text:", stops(notes)[0][2].replace("\n\n", " / "))
        await run(mon, 4)
        print("  four more checks          -> messages in total:", len(stops(notes)))
        SESSIONS["home"] = [stream()]
        await run(mon, 2)
        SESSIONS["home"] = []
        await run(mon, 2)
        print("  transcodes, ends          ->", len(warns(notes)), "alerts |", [s[2].split("\n\n")[1] for s in stops(notes)][1:])
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]
        await run(mon)
        SESSIONS["home"] = []
        await run(mon, 4)
        print("  a blip that never alerted -> alerts:", len(warns(notes)), "| messages:", len(stops(notes)))
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]
        await run(mon, 2)
        DOWN.add("home"); await run(mon, 5); DOWN.discard("home")
        print("  server down while alerted -> messages:", len(stops(notes)))
        again = TMO.TranscodeMonitor(notes)
        SESSIONS["home"] = []
        await run(again, 2)
        print("  stops after a new instance -> messages:", len(stops(notes)), "| alerts:", len(warns(notes)))
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]
        await run(mon, 2)
        SESSIONS["home"] = [stream(video_direct=True, reasons=("AudioCodecNotSupported",))]
        await run(mon, 2)
        print("  video direct, audio transcoded (audio alerts off) ->", [s[2].split("\n\n")[1] for s in stops(notes)])

        print("\n== audio only, and a server that does not say ==")
        notes, mon = fresh()
        SESSIONS["home"] = [stream(video_direct=True, reasons=("AudioCodecNotSupported",))]
        await run(mon, 3)
        print("  audio only, default     ->", len(notes.sent), "alert(s)")
        os.environ["JELLYFIN_TRANSCODE_AUDIO_ALERTS"] = "true"
        await run(mon, 2)
        print("  audio alerts on         ->", [(t) for _, t, _ in notes.sent], "|", notes.sent[-1][2].split("\n\n")[1])
        del os.environ["JELLYFIN_TRANSCODE_AUDIO_ALERTS"]
        notes, mon = fresh()
        SESSIONS["home"] = [stream(video_direct=None, reasons=())]
        await run(mon, 2)
        print("  no IsVideoDirect field  ->", len(notes.sent), "alert(s) |", notes.sent[-1][2].split("\n\n")[1])

        print("\n== a server that does not answer ==")
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]
        await run(mon, 2)
        DOWN.add("home"); await run(mon, 5); DOWN.discard("home")
        await run(mon, 2)
        print("  down for 5 checks, back  -> alerts in total:", len(notes.sent))
        DOWN.add("home"); SESSIONS["home"] = []; await run(mon, 5); DOWN.discard("home")
        SESSIONS["home"] = [stream()]; await run(mon, 3)
        print("  stopped while down, back -> alerts in total:", len(notes.sent), "(the stream was never seen to stop)")

        print("\n== state across a restart ==")
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]; await run(mon, 2)
        again = TMO.TranscodeMonitor(notes)
        await run(again, 4)
        print("  new instance, same stream -> alerts in total:", len(notes.sent))
        notes, mon = fresh()
        await run(mon)
        again = TMO.TranscodeMonitor(notes)
        await run(again)
        print("  one check, new instance, one more -> alerts:", len(notes.sent))

        print("\n== only your own server ==")
        notes, mon = fresh()
        SESSIONS["home"] = []; SESSIONS["friend"] = [stream()]
        CALLS.clear(); await run(mon, 4)
        print("  transcoding on the other server -> alerts:", len(notes.sent), "| servers asked:", sorted(set(CALLS)))
        SESSIONS["friend"] = []

        print("\n== off ==")
        notes, mon = fresh()
        SESSIONS["home"] = [stream()]
        os.environ["JELLYFIN_TRANSCODE_ALERTS"] = "false"
        CALLS.clear(); await run(mon, 4)
        print("  JELLYFIN_TRANSCODE_ALERTS=false -> requests:", CALLS, "| alerts:", len(notes.sent))
        del os.environ["JELLYFIN_TRANSCODE_ALERTS"]
        saved = {k: os.environ.pop(k) for k in [k for k in os.environ if k.startswith("JELLYFIN_1_")]}
        CALLS.clear(); await run(mon, 4)
        print("  no Jellyfin server configured   -> requests:", CALLS, "| alerts:", len(notes.sent))
        os.environ.update(saved)
    finally:
        await runner.cleanup()

asyncio.run(main())
