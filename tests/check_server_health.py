"""Power supplies and fans, the /server summary line, the weekly summary, the heartbeat, and scripts/watchdog.sh."""
import asyncio
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime
from pathlib import Path

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(IDRAC_HOST="h", IDRAC_USERNAME="u", IDRAC_PASSWORD="p", QBITTORRENT_HOST="h", QBITTORRENT_PORT=1,
                                   QBITTORRENT_TIMEOUT=5, QBITTORRENT_USERNAME="u", QBITTORRENT_PASSWORD="p", ALERT_CHANNEL_ID=10)
sys.modules["config.settings"] = c

import modules.idrac.hardware_service as HS
import modules.idrac.hardware_monitor as HM
import modules.general.weekly_summary_monitor as WS
import modules.general.heartbeat_monitor as HB
from core import activity_log
import modules.torrents.qbittorrent_service as QS
import modules.host.filesystem_service as FS
import modules.idrac.commands.server as SV
import isolate  # state files go to a temporary folder
GIB_ = 1024 ** 3
HM.HardwareMonitor.STATE_FILE = isolate.DATA / "hardware.json"
WS.WeeklySummaryMonitor.STATE_FILE = isolate.DATA / "weekly.json"
HB.HeartbeatMonitor.FILE = isolate.DATA / "heartbeat"

POWER = {"PowerSupplies": [
    {"Name": "PS1 Status", "MemberId": "PSU.Slot.1", "Status": {"Health": "OK", "State": "Enabled"}, "LastPowerOutputWatts": 95, "PowerCapacityWatts": 750},
    {"Name": "PS2 Status", "MemberId": "PSU.Slot.2", "Status": {"Health": "OK", "State": "Enabled"}, "PowerCapacityWatts": 750},
    {"MemberId": "PSU.Slot.3", "Status": {"State": "Absent"}}],
    "PowerControl": [{"Name": "System Power Control", "PowerConsumedWatts": 182}]}
THERMAL = {"Fans": [{"Name": "System Board Fan1", "Reading": 4800, "ReadingUnits": "RPM", "Status": {"Health": "OK", "State": "Enabled"}},
                    {"FanName": "System Board Fan2", "Reading": 4920, "Status": {"Health": "OK", "State": "Enabled"}}]}


class Notes:
    def __init__(s): s.sent = []; s.bot = None
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description, k.get("fields")))
        return send


def parsing():
    print("== reading iDRAC's answers ==")
    for name, comp in {**HS.power_supplies(POWER), **HS.fans(THERMAL)}.items():
        print(f"{name:18} {comp['kind']:12} {comp['health']:4} {comp['detail']}")
    print("power draw:", HS.power_draw(POWER))
    print("/server line:", SV.hardware_summary({"components": {**HS.power_supplies(POWER), **HS.fans(THERMAL)}, "power_watts": 182.0}))
    empty = {"PowerSupplies": [], "PowerControl": []}
    print("/server line, single unmonitored PSU:", SV.hardware_summary({"components": {**HS.power_supplies(empty), **HS.fans(THERMAL)}, "power_watts": HS.power_draw(empty)}))
    # As the real server answered: no supplies, no reading, board voltage sensors with health only.
    real = {"PowerSupplies": [], "PowerControl": [], "Voltages": [
        {"Name": "System Board 1.8V SW PG", "MemberId": "0", "ReadingVolts": None, "Status": {"Health": "OK", "State": "Enabled"}},
        {"Name": "System Board 2.5V SW PG", "MemberId": "1", "ReadingVolts": None, "Status": {"Health": "OK", "State": "Enabled"}},
        {"Name": "CPU1 VCORE PG", "MemberId": "2", "ReadingVolts": None, "Status": {"Health": None, "State": "Disabled"}}]}
    print("voltage sensors read:", list(HS.voltages(real)))
    print("/server line, real server:", SV.hardware_summary({"components": {**HS.power_supplies(real), **HS.fans(THERMAL), **HS.voltages(real)}, "power_watts": HS.power_draw(real)}))


async def hardware_monitor():
    print("\n== hardware monitor ==")
    notes = Notes(); mon = HM.HardwareMonitor(notes)
    data = {"components": {**HS.power_supplies(POWER), **HS.fans(THERMAL), "System Board 1.8V SW PG": {"kind": "Voltage", "health": "OK", "detail": ""}}, "power_watts": 182}
    async def health(): return data
    mon.service = types.SimpleNamespace(get_hardware_health=health)
    await mon.check(); print("first check, alerts:", len(notes.sent))
    data["components"]["PS2 Status"] = {"kind": "Power supply", "health": "Critical", "detail": ""}
    await mon.check()
    data["components"]["System Board Fan1"] = dict(data["components"]["System Board Fan1"], health="Warning")
    await mon.check()
    data["components"]["PS2 Status"] = {"kind": "Power supply", "health": "OK", "detail": ""}
    await mon.check()
    data["components"]["System Board 1.8V SW PG"] = {"kind": "Voltage", "health": "Critical", "detail": ""}
    await mon.check()
    del data["components"]["System Board Fan2"]
    await mon.check()
    await mon.check()
    for level, title, description, _ in notes.sent:
        print(f"  {level:9} {title} | {description.splitlines()[-1] if description else ''}")
    data["components"] = {}
    await mon.check(); print("iDRAC lists nothing -> new alerts:", len(notes.sent) - 5)


async def weekly():
    print("\n== weekly summary ==")
    notes = Notes(); mon = WS.WeeklySummaryMonitor(notes)
    clock = {"now": datetime(2026, 10, 11, 9, 0)}  # a Sunday
    mon._now = lambda: clock["now"]
    used = {"/": (40 * 1024 ** 3, 100 * 1024 ** 3), "/mnt/data": (3000 * 1024 ** 4 // 1000, 4 * 1024 ** 4)}

    async def used_bytes(self): return dict(used)
    async def torrents(self, category=None, filter_name=None):
        week_ago = clock["now"].timestamp() - 3 * 86400
        return [{"name": "Arrival.2016.1080p", "size": 8 * 1024 ** 3, "completion_on": int(week_ago)},
                {"name": "Old.Torrent", "size": 1, "completion_on": 1000}]
    FS.FilesystemService.get_used_bytes = used_bytes
    QS.QBittorrentService.get_torrents = torrents

    activity_log.record("jellyfin_copy", title="Gravity Falls — Season 2", server="Friend", files=20, bytes=30 * 1024 ** 3)
    activity_log.record("wan_outage", seconds=125)
    activity_log.record("wan_outage", seconds=600)
    for level, title in (("warning", "High Temperature — CPU1"), ("warning", "High Temperature — CPU1"), ("recovered", "Temperature Recovered — CPU1"), ("critical", "Power supply Failure — PS2"), ("success", "Torrent Completed")):
        activity_log.record("alert", level=level, title=title)

    await mon.check(); print("Sunday 09:00 -> summaries:", len(notes.sent))
    clock["now"] = datetime(2026, 10, 11, 10, 30)
    await mon.check(); print("Sunday 10:30 -> summaries:", len(notes.sent))
    await mon.check(); print("again the same day -> summaries:", len(notes.sent))
    for name, value, _ in notes.sent[-1][3]:
        print(f"  {name}: " + value.replace("\n", " / "))
    used["/"] = (52 * 1024 ** 3, 100 * 1024 ** 3)
    used["/mnt/data"] = (used["/mnt/data"][0] + 24 * 1024, used["/mnt/data"][1])  # 24 KiB of logs
    clock["now"] = datetime(2026, 10, 18, 10, 30)
    await mon.check()
    print("next Sunday disks:", notes.sent[-1][3][0][1].splitlines()[0])
    print("tiny change:", notes.sent[-1][3][0][1].splitlines()[1])
    print("one week of growth, no estimate yet:", "full in" not in notes.sent[-1][3][0][1])
    print("next Sunday copies:", notes.sent[-1][3][2][1])

    # A busy week: too many titles for the field.
    class Thread:
        def __init__(t): t.posts = []
        async def send(t, text): t.posts.append(text)
    thread, channel_posts = Thread(), []

    class Summary:
        channel = types.SimpleNamespace(send=lambda text: channel_posts.append(text))
        async def create_thread(m, name, auto_archive_duration): thread.name = name; return thread

    class BusyNotes(Notes):
        async def info(s, title, description="", **k): s.sent.append(("info", title, description, k.get("fields"))); return Summary()

    async def many(self, category=None, filter_name=None):
        start = clock["now"].timestamp() - 86400
        return [{"name": f"Some.Long.Movie.Title.Number.{n:02d}.20{n % 25:02d}.1080p.BluRay.x265-GROUP", "size": GIB_ * (n + 1),
                 "completion_on": int(start + n)} for n in range(40)]
    QS.QBittorrentService.get_torrents = many
    busy = BusyNotes(); mon2 = WS.WeeklySummaryMonitor(busy); mon2._now = mon._now
    mon2.state["last_run"] = None
    clock["now"] = datetime(2026, 10, 25, 10, 30)
    await mon2.check()
    field = busy.sent[-1][3][1][1]
    print("busy week field:", field.replace(chr(10), " / "))
    print("thread:", thread.name, "| messages:", len(thread.posts), "| lines:", sum(p.count("•") for p in thread.posts),
          "| longest message:", max(len(p) for p in thread.posts), "| first:", thread.posts[0].splitlines()[1])

    # Disk forecast: the first monitor has seen two Sundays (40, 52 GiB);
    # a third with the same growth gives an estimate.
    QS.QBittorrentService.get_torrents = torrents
    used["/"] = (64 * 1024 ** 3, 100 * 1024 ** 3)
    clock["now"] = datetime(2026, 11, 1, 10, 30)
    await mon.check()
    print("third Sunday:", notes.sent[-1][3][0][1].splitlines()[0])
    print("  steady disk has no estimate:", "full in" not in notes.sent[-1][3][0][1].splitlines()[1])
    WSM = WS.WeeklySummaryMonitor
    week = 7 * 86400
    print("  slow growth:", WSM._forecast([[0, 10 * 1024 ** 3], [week, 11 * 1024 ** 3], [2 * week, 12 * 1024 ** 3]], 12 * 1024 ** 3, 100 * 1024 ** 3))
    print("  months away:", WSM._forecast([[0, 0], [week, 10 * 1024 ** 3], [2 * week, 20 * 1024 ** 3]], 20 * 1024 ** 3, 400 * 1024 ** 3))
    print("  nearly full:", WSM._forecast([[0, 0], [week, 50 * 1024 ** 3], [2 * week, 95 * 1024 ** 3]], 95 * 1024 ** 3, 100 * 1024 ** 3))


def drives():
    print("\n== drive status ==")
    from modules.idrac.storage_monitor import StorageMonitor as SM
    for label, args in (("healthy HDD", ("OK", "Online", False, None)), ("SSD 64% left", ("OK", "Online", False, 64)),
                        ("SSD 9% left", ("OK", "Online", False, 9)), ("failure predicted", ("OK", "Online", True, 80))):
        print(f"  {label:18} -> {SM._get_drive_status(*args)}")
    drive = types.SimpleNamespace(media_life_left=9)
    print("  alert line:", SM._life_line(drive).strip().replace(chr(10), " "))


async def heartbeat():
    print("\n== heartbeat ==")
    bot = types.SimpleNamespace(is_closed=lambda: False, latency=0.05)
    mon = HB.HeartbeatMonitor(types.SimpleNamespace(bot=bot))
    await mon.check(); print("connected -> written:", HB.HeartbeatMonitor.FILE.exists())
    HB.HeartbeatMonitor.FILE.unlink()
    bot.latency = float("inf")
    await mon.check(); print("disconnected -> written:", HB.HeartbeatMonitor.FILE.exists())


def watchdog():
    print("\n== scripts/watchdog.sh ==")
    root = Path(tempfile.mkdtemp(prefix="aegis-wd-"))
    project, fakebin = root / "Aegis-Bot", root / "bin"
    (project / "scripts").mkdir(parents=True); (project / "data").mkdir(); fakebin.mkdir()
    shutil.copy(Path(__file__).resolve().parent.parent / "scripts" / "watchdog.sh", project / "scripts")
    (project / ".env").write_text("DISCORD_TOKEN=x\nWATCHDOG_WEBHOOK_URL=https://discord.example/api/webhooks/1/abc\n")
    sent = root / "sent.txt"
    for name, body in (("docker", 'cat "$FAKE_RUNNING"\n'), ("curl", f'for a in "$@"; do last="$a"; done\nprintf "%s\\t%s\\n" "$7" "$last" >> "{sent}"\n')):
        path = fakebin / name
        path.write_text("#!/bin/bash\n" + body); path.chmod(path.stat().st_mode | stat.S_IEXEC)
    running = root / "running"
    home = root / "home"; home.mkdir()
    env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", FAKE_RUNNING=str(running), HOME=str(home))
    env.pop("XDG_STATE_HOME", None)

    def run(label, is_running, beat_age):
        running.write_text("true" if is_running else "false")
        beat = project / "data" / "heartbeat"
        if beat_age is None:
            beat.unlink(missing_ok=True)
        else:
            beat.write_text(str(int(time.time()) - beat_age))
        before = sent.read_text().count("\n") if sent.exists() else 0
        result = subprocess.run(["bash", str(project / "scripts" / "watchdog.sh")], env=env, capture_output=True, text=True)
        lines = sent.read_text().splitlines() if sent.exists() else []
        new = lines[before:]
        print(f"  {label:30} exit={result.returncode} sent={[l.split(chr(9))[0][13:75] for l in new]}")

    run("running, fresh heartbeat", True, 30)
    run("running, heartbeat 15 min old", True, 900)
    run("still stale (no repeat)", True, 960)
    run("fresh again", True, 10)
    run("container stopped", False, 10)
    run("container back", True, 5)
    print("  webhook used:", sent.read_text().splitlines()[0].split(chr(9))[-1])
    print("  state kept in home:", (home / ".local/state/aegis-bot/watchdog_state").read_text().strip(), "| written to data/:", (project / "data" / "watchdog_state").exists())
    shutil.rmtree(root, ignore_errors=True)


async def main():
    parsing()
    await hardware_monitor()
    await weekly()
    drives()
    await heartbeat()
    watchdog()

asyncio.run(main())
