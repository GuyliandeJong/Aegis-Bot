"""A finished torrent is not announced again after a power cut: checking files, missing files and a recheck show it as incomplete for a while, but only a real download (or a new completion time) allows another "Torrent Completed"."""
import asyncio
import json
import logging
import sys
import tempfile
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.ERROR)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(QBITTORRENT_HOST="127.0.0.1", QBITTORRENT_PORT=1, QBITTORRENT_TIMEOUT=5,
                                   QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", DISCORD_USER_ID="")
sys.modules["config.settings"] = c

import modules.torrents.torrent_monitor as TM
import isolate  # state files go to a temporary folder

TM.library_followup.start = lambda *a, **k: None
FOLDER = Path(tempfile.mkdtemp(prefix="recheck-", dir=str(isolate.DATA)))
TM.TorrentMonitor.STATE_FILE = FOLDER / "state.json"


class Notes:
    def __init__(s): s.titles = []
    async def success(s, title, description="", **k): s.titles.append(description.split("**")[1])


class Service:
    def __init__(s): s.torrents = []
    async def get_torrents_snapshot(s): return [dict(t) for t in s.torrents]


def torrent(name, state, progress, completion_on=0, number=1):
    return {"hash": f"{number:040x}", "name": name, "state": state, "progress": progress, "completion_on": completion_on,
            "category": "PC", "size": 10, "downloaded": 10, "uploaded": 20, "time_active": 100, "dlspeed": 0, "ratio": 2.0, "added_on": 5}


async def main():
    notes = Notes(); mon = TM.TorrentMonitor(notes); mon.service = Service()

    async def step(*torrents):
        mon.service.torrents = list(torrents)
        await mon.check()

    def alerts(): return list(notes.titles)

    print("== a normal download ==")
    await step(torrent("Alpha", "downloading", 0.5))
    print("  downloading          ->", alerts())
    await step(torrent("Alpha", "uploading", 1.0, 1000))
    print("  completed            ->", alerts())
    await step(torrent("Alpha", "stalledUP", 1.0, 1000))
    print("  seeding              ->", alerts())

    print("\n== after a power cut ==")
    for state, progress in [("checkingResumeData", 0.0), ("checkingUP", 0.4), ("checkingUP", 0.9)]:
        await step(torrent("Alpha", state, progress, 1000))
    print("  while checking files ->", alerts(), "| notified kept:", mon.state[f"{1:040x}"]["notified"])
    await step(torrent("Alpha", "stalledUP", 1.0, 1000))
    print("  done checking        ->", alerts())
    await step(torrent("Alpha", "missingFiles", 0.0, 1000))
    await step(torrent("Alpha", "error", 0.0, 1000))
    await step(torrent("Alpha", "stalledUP", 1.0, 1000))
    print("  missing files, back  ->", alerts())

    print("\n== a state file from before the fix ==")
    key = f"{1:040x}"
    saved = json.loads(TM.TorrentMonitor.STATE_FILE.read_text())
    saved[key].pop("announced_on", None)
    TM.TorrentMonitor.STATE_FILE.write_text(json.dumps(saved))
    old = TM.TorrentMonitor(notes); old.service = mon.service
    mon.service.torrents = [torrent("Alpha", "checkingUP", 0.3, 1000)]; await old.check()
    mon.service.torrents = [torrent("Alpha", "stalledUP", 1.0, 1000)]; await old.check()
    print("  recheck, no stored time ->", alerts(), "| time recorded:", old.state[key].get("announced_on"))
    mon = old

    print("\n== downloaded again ==")
    await step(torrent("Alpha", "downloading", 0.2, 0))
    print("  downloading again    ->", alerts(), "| may notify again:", mon.state[key]["notified"] is False)
    await step(torrent("Alpha", "uploading", 1.0, 2000))
    print("  completed, new time  ->", alerts())

    print("\n== a few pieces fetched again after a crash ==")
    await step(torrent("Beta", "uploading", 1.0, 3000, number=2))
    await step(torrent("Beta", "stalledUP", 1.0, 3000, number=2))
    base = len(alerts())
    for progress in (0.99, 0.995):
        await step(torrent("Beta", "downloading", progress, 0, number=2))
    await step(torrent("Beta", "uploading", 1.0, 3000, number=2))
    print("  same completion time  -> new alerts:", len(alerts()) - base)

    print("\n== a torrent never announced ==")
    await step(torrent("Gamma", "downloading", 0.7, 0, number=3))
    await step(torrent("Gamma", "checkingDL", 0.7, 0, number=3))
    await step(torrent("Gamma", "uploading", 1.0, 4000, number=3))
    print("  finished while the bot was busy ->", alerts()[-1:], "| time recorded:", mon.state[f"{3:040x}"].get("announced_on"))
    print("  all alerts:", alerts())

asyncio.run(main())
