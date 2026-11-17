"""SMART disk monitor with a fake smartctl: healthy and failing disks, sector counters (baseline, growth, pending), NVMe wear, critical warnings and media errors, unreadable and sleeping disks, smartctl missing, several disks, and state across a restart."""
import asyncio
import json
import logging
import os
import stat
import sys
import tempfile
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
for key in ("SMART_DEVICES", "SMART_NVME_WEAR"):
    os.environ.pop(key, None)

import modules.host.smart_monitor as SM
import isolate  # state files go to a temporary folder

BIN = Path(tempfile.mkdtemp(prefix="smart-bin-", dir=str(isolate.DATA)))
DATA = Path(tempfile.mkdtemp(prefix="smart-data-", dir=str(isolate.DATA)))
SCRIPT = BIN / "smartctl"
SCRIPT.write_text(f"""#!{sys.executable}
import sys
from pathlib import Path
name = Path(sys.argv[-1]).name
data = Path({str(DATA)!r}) / (name + ".json")
if data.exists():
    sys.stdout.write(data.read_text())
sys.exit(0)
""")
SCRIPT.chmod(SCRIPT.stat().st_mode | stat.S_IEXEC)
ORIGINAL_PATH = os.environ["PATH"]
os.environ["PATH"] = f"{BIN}:{ORIGINAL_PATH}"


def ata(name, passed=True, reallocated=0, pending=0, uncorrectable=0, model="Example SSD 500", temperature=34):
    (DATA / f"{name}.json").write_text(json.dumps({
        "model_name": model, "temperature": {"current": temperature}, "smart_status": {"passed": passed},
        "ata_smart_attributes": {"table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": reallocated}},
            {"id": 197, "name": "Current_Pending_Sector", "raw": {"value": pending}},
            {"id": 198, "name": "Offline_Uncorrectable", "raw": {"value": uncorrectable}}]}}))


def nvme(name, used=5, critical=0, errors=0):
    (DATA / f"{name}.json").write_text(json.dumps({
        "model_name": "Example NVMe 1TB", "temperature": {"current": 41}, "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {"critical_warning": critical, "percentage_used": used, "media_errors": errors}}))


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send
    def titles(s): return [(lvl, t) for lvl, t, _ in s.sent]


_files = [0]


def fresh():
    _files[0] += 1
    SM.SmartMonitor.STATE_FILE = isolate.DATA / f"smart-{_files[0]}.json"
    notes = Notes()
    for f in DATA.glob("*.json"): f.unlink()
    return notes, SM.SmartMonitor(notes)


async def run(mon, count=1):
    for _ in range(count):
        await mon.check()


async def main():
    print("== off ==")
    notes, mon = fresh()
    await run(mon)
    print("  SMART_DEVICES empty ->", notes.titles())

    print("\n== a healthy SATA disk ==")
    os.environ["SMART_DEVICES"] = "/dev/sda"
    notes, mon = fresh()
    ata("sda")
    await run(mon, 3)
    print("  3 checks ->", notes.titles())

    print("\n== sectors ==")
    ata("sda", reallocated=0)
    notes, mon = fresh(); ata("sda", reallocated=12)
    await run(mon)
    print("  old reallocated sectors at the first look ->", notes.titles())
    ata("sda", reallocated=12); await run(mon)
    ata("sda", reallocated=15); await run(mon)
    print("  12 -> 15 ->", notes.titles(), "|", notes.sent[-1][2].split(") ", 1)[1])
    await run(mon)
    print("  unchanged ->", len(notes.sent), "alert(s)")
    ata("sda", reallocated=16); await run(mon)
    print("  15 -> 16 ->", len(notes.sent), "alert(s)")
    ata("sda", reallocated=16, pending=2, uncorrectable=1); await run(mon, 3)
    print("  pending sectors ->", notes.titles()[2:], "|", notes.sent[-1][2].split(") ", 1)[1])
    ata("sda", reallocated=16); await run(mon)
    ata("sda", reallocated=16, pending=1); await run(mon)
    print("  cleared, then again ->", len(notes.sent), "alerts in total")

    print("\n== health ==")
    notes, mon = fresh(); ata("sda", passed=False, temperature=51)
    await run(mon, 3)
    print("  failing ->", notes.titles(), "|", notes.sent[0][2])
    ata("sda", passed=True); await run(mon)
    print("  passing again ->", notes.titles()[1:])

    print("\n== NVMe ==")
    os.environ["SMART_DEVICES"] = "/dev/nvme0n1"
    notes, mon = fresh(); nvme("nvme0n1", used=95)
    await run(mon, 2)
    print("  95% used ->", notes.titles(), "|", notes.sent[0][2].split(") ", 1)[1])
    nvme("nvme0n1", used=95, critical=4); await run(mon, 2)
    print("  critical flag ->", notes.titles()[1:])
    nvme("nvme0n1", used=10, critical=0, errors=0); await run(mon)
    nvme("nvme0n1", used=10, errors=3); await run(mon)
    print("  media errors 0 -> 3 ->", notes.titles()[2:])
    os.environ["SMART_NVME_WEAR"] = "50"
    nvme("nvme0n1", used=60, errors=3); await run(mon)
    print("  SMART_NVME_WEAR=50 at 60% ->", notes.titles()[3:])
    del os.environ["SMART_NVME_WEAR"]

    print("\n== two disks ==")
    os.environ["SMART_DEVICES"] = "/dev/sda, /dev/sdb"
    notes, mon = fresh(); ata("sda"); ata("sdb", passed=False, model="Example HDD 4TB")
    await run(mon)
    print("  ->", notes.titles(), "|", notes.sent[0][2].split(" reports")[0])

    print("\n== unreadable and sleeping ==")
    os.environ["SMART_DEVICES"] = "/dev/sdc"
    notes, mon = fresh()
    await run(mon, 2)
    print("  2 unreadable checks ->", notes.titles())
    await run(mon)
    print("  3rd ->", notes.titles())
    await run(mon, 3)
    print("  more ->", len(notes.sent), "alert(s)")
    ata("sdc"); await run(mon); (DATA / "sdc.json").unlink(); await run(mon, 2)
    print("  readable once, then 2 unreadable ->", len(notes.sent), "alert(s)")
    notes, mon = fresh()
    (DATA / "sdc.json").write_text(json.dumps({"smartctl": {"exit_status": 2, "messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}}))
    await run(mon, 5)
    print("  asleep for 5 checks ->", notes.titles())

    print("\n== a restart of the bot ==")
    os.environ["SMART_DEVICES"] = "/dev/sda"
    notes, mon = fresh(); ata("sda", reallocated=4)
    await run(mon)
    again = SM.SmartMonitor(notes); ata("sda", reallocated=9)
    await run(again)
    print("  baseline kept by a new instance ->", notes.titles())

    print("\n== smartctl missing ==")
    os.environ["PATH"] = ORIGINAL_PATH.replace(str(BIN), "")
    SCRIPT.rename(BIN / "smartctl.off")
    notes, mon = fresh()
    await run(mon, 3)
    print("  no smartctl -> alerts:", notes.titles(), "| noted once:", mon.missing_logged)
asyncio.run(main())
