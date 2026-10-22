"""The weekly summary's "Container updates" field: Watchtower's log read through a local fake Portainer, matching images to containers, the week's window, overflow to the thread, and the fallbacks when Portainer or Watchtower is missing."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime, timezone

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="h", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5, QBITTORRENT_USERNAME="u",
                                   QBITTORRENT_PASSWORD="p", ALERT_CHANNEL_ID=10)
sys.modules["config.settings"] = c
PORT = 18165
for key in [k for k in os.environ if k.startswith(("PORTAINER_", "WATCHTOWER_"))]:
    del os.environ[key]

import modules.containers.portainer_service as PS
import modules.containers.watchtower_updates as WU
import modules.torrents.qbittorrent_service as QS
import modules.host.filesystem_service as FS
import modules.general.weekly_summary_monitor as WS
import isolate  # state files go to a temporary folder
WS.WeeklySummaryMonitor.STATE_FILE = isolate.DATA / "weekly.json"

# name: [id, image]; jellyfin and sonarr were updated, mosquitto restarted with homeassistant.
CONTAINERS = {
    "watchtower": ["w" * 64, "containrrr/watchtower"],
    "jellyfin": ["j" * 64, "jellyfin/jellyfin"],
    "sonarr": ["s" * 64, "lscr.io/linuxserver/sonarr:latest"],
    "homeassistant": ["h" * 64, "ghcr.io/home-assistant/home-assistant:stable"],
    "mosquitto": ["m" * 64, "eclipse-mosquitto:2"],
    "radarr": ["r" * 64, "sha256:0123456789abcdef"],
}
NOW = datetime(2026, 10, 18, 10, 30, tzinfo=timezone.utc).timestamp()
SINCE = NOW - 7 * 86400
REQUESTS = []


def stamp(days_ago, seconds=0):
    when = datetime.fromtimestamp(NOW - days_ago * 86400 + seconds, timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + "123456789Z"


def logfmt(days_ago, seconds, level, msg, extra=""):
    when = datetime.fromtimestamp(NOW - days_ago * 86400 + seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'{stamp(days_ago, seconds)} time="{when}" level={level} msg="{msg}"{extra}\n'


# As Watchtower 1.7 logs with WATCHTOWER_LOG_FORMAT=logfmt (stderr, no TTY).
LOG = [
    # Ten days ago: before the summary's week, must be ignored.
    logfmt(10, 0, "info", "Found new linuxserver/qbittorrent:latest image (aa11bb22cc33)"),
    logfmt(10, 5, "info", "Stopping /qbittorrent (q1q2q3q4q5q6) with SIGTERM"),
    logfmt(10, 9, "info", "Creating /qbittorrent"),
    logfmt(10, 11, "info", "Session done", " Failed=0 Scanned=28 Updated=1 notify=no"),
    # Five days ago: nothing new.
    logfmt(5, 0, "info", "Session done", " Failed=0 Scanned=28 Updated=0 notify=no"),
    # Three days ago: two updates; all "Found new" first, creates in dependency order.
    logfmt(3, 0, "info", "Found new jellyfin/jellyfin:latest image (8b2e1f0c4d3a)"),
    logfmt(3, 2, "info", "Found new lscr.io/linuxserver/sonarr:latest image (5f6e7d8c9b0a)"),
    logfmt(3, 20, "info", "Stopping /sonarr (s1s2s3s4s5s6) with SIGTERM"),
    logfmt(3, 31, "info", "Stopping /jellyfin (j1j2j3j4j5j6) with SIGTERM"),
    logfmt(3, 40, "info", "Creating /sonarr"),
    logfmt(3, 42, "info", "Creating /jellyfin"),
    logfmt(3, 45, "info", "Removing image 1a2b3c4d5e6f"),
    logfmt(3, 46, "info", "Session done", " Failed=0 Scanned=28 Updated=2 notify=no"),
    # One day ago: homeassistant, with mosquitto restarted for the link; radarr's tag has moved on since.
    logfmt(1, 0, "info", "Found new ghcr.io/home-assistant/home-assistant:stable image (ab12cd34ef56)"),
    logfmt(1, 1, "info", "Found new lscr.io/linuxserver/radarr:latest image (0f1e2d3c4b5a)"),
    logfmt(1, 10, "info", "Stopping /radarr (r1r2r3r4r5r6) with SIGTERM"),
    logfmt(1, 12, "info", "Stopping /homeassistant (h1h2h3h4h5h6) with SIGTERM"),
    logfmt(1, 14, "info", "Stopping /mosquitto (m1m2m3m4m5m6) with SIGTERM"),
    logfmt(1, 20, "info", "Creating /mosquitto"),
    logfmt(1, 22, "info", "Creating /homeassistant"),
    logfmt(1, 25, "info", "Creating /radarr"),
    logfmt(1, 30, "info", "Session done", " Failed=0 Scanned=28 Updated=3 notify=no"),
]


def frame(stream, text):
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


async def docker(request):
    path = request.match_info["path"]
    REQUESTS.append((request.method, path, dict(request.query)))
    if path == "containers/json":
        return web.json_response([{"Id": i, "Names": [f"/{n}"], "Image": img, "State": "running", "Status": "Up 1 day"}
                                  for n, (i, img) in CONTAINERS.items()])
    if path == f"containers/{'w' * 64}/json":
        return web.json_response({"State": {"Status": "running", "Running": True}, "Config": {"Image": "containrrr/watchtower", "Tty": False}})
    if path == f"containers/{'w' * 64}/logs":
        # Like Docker without a TTY: one frame per line, on stderr. The fake ignores since, so the parser's own filter is tested.
        return web.Response(body=b"".join(frame(2, line) for line in LOG))
    return web.json_response({"message": "No such container"}, status=404)


async def main():
    app = web.Application(); app.add_routes([web.route("*", "/api/endpoints/2/docker/{path:.*}", docker)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    mon = WS.WeeklySummaryMonitor(None)
    try:
        print("== not configured ==")
        mon.overflow = []
        print("  field:", await mon._container_updates(SINCE), "| requests:", len(REQUESTS))

        os.environ.update({"PORTAINER_URL": f"http://127.0.0.1:{PORT}", "PORTAINER_API_KEY": "k", "PORTAINER_ENDPOINT_ID": "2"})

        print("\n== watchtower not found ==")
        os.environ["WATCHTOWER_CONTAINER"] = "watchtower-other"
        print("  no such container:", await mon._container_updates(SINCE))
        del os.environ["WATCHTOWER_CONTAINER"]

        print("\n== a week of Watchtower's log ==")
        PS._list_cache["data"] = None
        field = await mon._container_updates(SINCE)
        for line in field.splitlines():
            print("  |", line)
        logs = [r for r in REQUESTS if r[1].endswith("/logs")][-1]
        print("  logs request: since", logs[2].get("since") == str(int(SINCE)), "| timestamps", logs[2].get("timestamps"),
              "| stdout+stderr", logs[2].get("stdout"), logs[2].get("stderr"))
        print("  qbittorrent from ten days ago left out:", "qbittorrent" not in field)

        print("\n== other log formats ==")
        pretty = (f"{stamp(2)} INFO[0003] Found new nginx:latest image (1234567890ab)\n"
                  f"{stamp(2, 5)} INFO[0008] Creating /web\n{stamp(2, 6)} INFO[0009] Session done   Failed=0 Scanned=3 Updated=1 notify=no\n")
        json_ = f'{stamp(2)} {{"level":"info","msg":"Found new nginx:latest image (1234567890ab)","time":"x"}}\n{stamp(2, 5)} {{"level":"info","msg":"Creating /web","time":"x"}}\n'
        for label, text in (("pretty", pretty), ("json", json_)):
            print(f"  {label}:", [(u["name"], u["image"]) for u in WU.parse(text, {"web": "nginx"}, SINCE)])
        print("  container gone, one new image:", [(u["name"], u["image"]) for u in WU.parse(json_, {}, SINCE)])
        print("  no timestamp prefix is skipped:", WU.parse('level=info msg="Creating /web"\n', {}, 0))

        print("\n== a busy week ==")
        LOG[:] = [logfmt(2, n * 10, "info", f"Found new example/app{n:02d}:latest image (abcdef{n:06d})") for n in range(40)]
        LOG.extend(logfmt(2, 500 + n, "info", f"Creating /app{n:02d}") for n in range(40))
        LOG.append(logfmt(2, 600, "info", "Session done", " Failed=0 Scanned=40 Updated=40 notify=no"))
        CONTAINERS.update({f"app{n:02d}": [f"{n:064d}", f"example/app{n:02d}"] for n in range(40)})
        PS._list_cache["data"] = None
        mon.overflow = []
        print("  field:", (await mon._container_updates(SINCE)).replace("\n", " / "))
        print("  thread list:", mon.overflow[0][0], "|", len(mon.overflow[0][1]), "lines | first:", mon.overflow[0][1][0])

        print("\n== Portainer fails ==")
        os.environ["PORTAINER_URL"] = "http://127.0.0.1:1"; PS._list_cache["data"] = None
        print("  field:", await mon._container_updates(SINCE))
        for key in ("PORTAINER_URL", "PORTAINER_API_KEY", "PORTAINER_ENDPOINT_ID"):
            del os.environ[key]

        print("\n== place in the summary ==")
        async def used_bytes(self): return {}
        async def torrents(self, category=None, filter_name=None): return []
        FS.FilesystemService.get_used_bytes = used_bytes
        QS.QBittorrentService.get_torrents = torrents
        fields = await mon.build(SINCE)
        print("  last two fields:", [name for name, _, _ in fields[-2:]], "| value:", fields[-1][1])
    finally:
        await runner.cleanup()

asyncio.run(main())
