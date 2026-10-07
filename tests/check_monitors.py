"""CPU/memory threshold monitors, the alert cooldown, and temperature recovery."""
import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

import stubs
from stubs import mod

S = stubs.settings
for p in ("CPU", "MEMORY"):
    setattr(S, f"{p}_WARNING", 80); setattr(S, f"{p}_CRITICAL", 95)
    setattr(S, f"{p}_WARNING_DURATION", 300); setattr(S, f"{p}_CRITICAL_DURATION", 120)
    setattr(S, f"{p}_RECOVERY_THRESHOLD", 70); setattr(S, f"{p}_CRITICAL_RECOVERY_THRESHOLD", 90)
    setattr(S, f"{p}_RECOVERY_DURATION", 180)
S.FILESYSTEM_WARNING = 80; S.FILESYSTEM_CRITICAL = 90
S.TEMPERATURE_WARNING = 70; S.TEMPERATURE_CRITICAL = 85
S.MONITOR_STALE_DURATION = 600


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description, **k): s.sent.append((level, title, description))
        return send


class FakeState:
    """In-memory MonitorState: the real class with file access disabled."""


CLOCK = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None): return CLOCK["now"]


# real MonitorState, but never touching /app/data
src = open("monitors/state.py", encoding="utf-8").read().replace("state = MonitorState()", "")
ns = {}
exec(compile(src, "state.py", "exec"), ns)
ns["datetime"] = FakeDT
MS = ns["MonitorState"]
MS.load = lambda self: None
MS.save = lambda self: None
mod("monitors.state", state=MS())
state = sys.modules["monitors.state"].state

usage = {"v": 0.0}
mem = types.SimpleNamespace(percent_used=0.0, used="1 GiB", available="2 GiB", total="3 GiB")


class CPUService:
    async def get_cpu_status(s): return types.SimpleNamespace(cpu=types.SimpleNamespace(usage_percent=usage["v"]))


class MemoryService:
    async def get_memory_status(s):
        mem.percent_used = usage["v"]; return types.SimpleNamespace(memory=mem)


mod("services.cpu_service", CPUService=CPUService)
mod("services.memory_service", MemoryService=MemoryService)


SEQ = [50]*3 + [85]*8 + [97]*4 + [92]*2 + [85]*5 + [60]*5 + [85]*2 + [60]*6 + [99]*4 + [50]*8


def run(cls, attr):
    notes = Notes(); m = cls(notes)
    m._now = staticmethod(lambda: CLOCK["now"])
    setattr(state, attr, {})
    CLOCK["now"] = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for v in SEQ:
        usage["v"] = float(v); asyncio.run(m.check()); CLOCK["now"] += timedelta(seconds=60)
    return notes.sent, dict(getattr(state, attr))


import monitors.cpu_monitor as new_cpu, monitors.memory_monitor as new_mem

# The sequence above must produce exactly these notifications, in order.
EXPECTED = ["High", "Critical", "High", "Normal", "Critical", "Recovered", "Normal"]

for new, cls, attr, label in ((new_cpu, "CPUMonitor", "cpu", "CPU"), (new_mem, "MemoryMonitor", "memory", "Memory")):
    sent, final = run(getattr(new, cls), attr)
    titles = [title for _, title, _ in sent]
    assert titles == [f"{label} Usage {word}" for word in EXPECTED], titles
    assert final.get("status") == "healthy", final
    print(f"{cls}: sent {len(sent)} notifications: {[t.split()[-1] for t in titles]}")

# ---- cooldown: filesystem
fs = {"pct": 0}


class FilesystemService:
    async def get_filesystems(s):
        f = types.SimpleNamespace(mountpoint="/mnt/data", percent_used=fs["pct"], used="u", free="f", total="t")
        return types.SimpleNamespace(filesystems=[f])


mod("services.filesystem_service", FilesystemService=FilesystemService)
from monitors.filesystem_monitor import FilesystemMonitor


def fs_run(seq, minutes_per_step=1):
    state.filesystems = {}; state.alerts = {}
    notes = Notes(); m = FilesystemMonitor(notes)
    out = []
    for v in seq:
        fs["pct"] = v; before = len(notes.sent); asyncio.run(m.check())
        out.append(notes.sent[-1][0] if len(notes.sent) > before else ".")
        CLOCK["now"] += timedelta(minutes=minutes_per_step)
    return " ".join(out)


print("\nFilesystem, one check per minute, default 30-minute cooldown")
print("  flapping 79/81 x20        :", fs_run([79] + [81, 79] * 20))
print("  rises and stays at 81     :", fs_run([79, 81] + [81] * 5))
print("  flaps, then settles at 81 :", fs_run([79, 81, 79, 81, 79] + [81] * 32))
print("  warning then critical     :", fs_run([79, 81, 79, 95, 95]))
print("  flap, settle healthy      :", fs_run([79, 81, 79] + [79] * 31))

# ---- temperature recovery + legacy "ok"
temp = {"c": 0.0}


class TemperatureService:
    async def get_temperature_status(s):
        return types.SimpleNamespace(temperatures=[types.SimpleNamespace(name="CPU1 Temp", reading=temp["c"])])


mod("services.temperature_service", TemperatureService=TemperatureService)
mod("utils.status", temperature_status=lambda r: "Critical" if r >= 85 else "Warning" if r >= 70 else "OK")
from monitors.temperature_monitor import TemperatureMonitor


def t_run(seq, initial=None, step=40):
    state.temperatures = dict(initial or {}); state.alerts = {}; state.last_success = {}; state.stale = {}
    notes = Notes(); m = TemperatureMonitor(notes); out = []
    for v in seq:
        temp["c"] = v; before = len(notes.sent); asyncio.run(m.check())
        out.append(notes.sent[-1][1].split(" — ")[0] if len(notes.sent) > before else ".")
        CLOCK["now"] += timedelta(minutes=step)
    return " | ".join(out), dict(state.temperatures)


print("\nTemperature (checks 40 minutes apart, so cooldown never applies)")
print("  50 -> 75 -> 50            :", t_run([50, 75, 50]))
print("  legacy state 'ok', 50, 50 :", t_run([50, 50], {"CPU1 Temp": "ok"}))
print("  75 -> 90 -> 75 -> 50      :", t_run([50, 75, 90, 75, 50])[0])
