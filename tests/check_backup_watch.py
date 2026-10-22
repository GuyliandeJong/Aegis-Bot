"""The backup monitor: alerts once when the last successful backup is too old, says why (failed run or no run), recovers once, remembers the last success across a failed run, and stays quiet when backups are not set up."""
import asyncio
import json
import os
import sys
import types

m = types.ModuleType("utils.logger"); m.logger = types.SimpleNamespace(warning=print, error=print, info=print); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
os.environ.pop("BACKUP_MAX_AGE_DAYS", None)
os.environ.pop("BACKUP_PATHS", None)

import monitors.backup_monitor as BM
import isolate  # state files go to a temporary folder
BM.BackupMonitor.STATUS_FILE = isolate.DATA / "backup_status.json"
BM.BackupMonitor.STATE_FILE = isolate.DATA / "backup_monitor.json"
DAY = 86400
T0 = 1_800_000_000


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


def status(day, ok, error=""):
    BM.BackupMonitor.STATUS_FILE.write_text(json.dumps({"time": T0 + day * DAY, "ok": ok, "error": error}))


async def main():
    notes = Notes(); mon = BM.BackupMonitor(notes)
    clock = {"day": 0}
    mon._now = lambda: T0 + clock["day"] * DAY

    async def at(day, label):
        clock["day"] = day
        before = len(notes.sent)
        await mon.check()
        new = notes.sent[before:]
        print(f"  day {day:>4}: {label:34} -> {[(l, t) for l, t, _ in new]}")
        return new

    print("== not set up ==")
    status(0, True); await at(30, "no BACKUP_PATHS")

    os.environ["BACKUP_PATHS"] = "/srv"
    mon.state = {"last_ok": None, "alerted": False}
    print("\n== weekly backups ==")
    await at(0, "success on day 0")
    status(7, True); await at(7.1, "success on day 7")
    await at(14, "day 14, the day-14 run is missing")
    new = await at(15.2, "8 days since the last success")
    print("   says:", new[0][2].split("\n\n")[1][:60])
    await at(16, "still none")
    status(17, True); await at(17.1, "a backup succeeds")

    print("\n== failing runs ==")
    status(24, False, "upload to proton:Backups/home failed: 502"); await at(24.1, "run fails")
    new = await at(25.2, "8 days since the last success")
    print("   says:", new[0][2].split("\n\n")[1][:70])

    print("\n== after a bot restart ==")
    again = BM.BackupMonitor(notes); again._now = mon._now; before = len(notes.sent)
    await again.check()
    print("  last success kept:", again.state["last_ok"] == T0 + 17 * DAY, "| no repeat:", notes.sent[before:] == [])
    os.environ["BACKUP_MAX_AGE_DAYS"] = "0"; before = len(notes.sent)
    clock["day"] = 60; await again.check()
    print("  BACKUP_MAX_AGE_DAYS=0 ->", notes.sent[before:])

asyncio.run(main())
