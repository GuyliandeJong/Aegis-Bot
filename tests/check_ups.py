"""UPS monitor against a fake NUT server: outage, low battery, restored with its duration, replace battery, an unreachable server (and an outage that ends while it is unreachable), an unknown UPS, missing variables, state across a restart, the weekly power line, and the module being off without NUT_HOST."""
import asyncio
import logging
import os
import sys
import time
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18203
for key in [k for k in os.environ if k.startswith("NUT_")]:
    del os.environ[key]
os.environ.update({"NUT_HOST": "127.0.0.1", "NUT_PORT": str(PORT), "NUT_UPS": "ups"})

from core import activity_log
import modules.ups.ups_monitor as UM
from modules.ups.nut_service import NutError, NutService
import isolate  # state files go to a temporary folder

VARS = {"ups.status": "OL", "battery.charge": "100", "battery.runtime": "1800", "ups.load": "23"}
CLOCK = [1_000_000.0]
UM.UpsMonitor._now = staticmethod(lambda: CLOCK[0])
ASKED = []


async def handle(reader, writer):
    while True:
        line = (await reader.readline()).decode().strip()
        if not line:
            break
        ASKED.append(line)
        parts = line.split()
        if parts[0] == "GET" and parts[1] == "VAR":
            if parts[2] != "ups":
                writer.write(b"ERR UNKNOWN-UPS\n")
            elif parts[3] not in VARS:
                writer.write(b"ERR VAR-NOT-SUPPORTED\n")
            else:
                writer.write(f'VAR ups {parts[3]} "{VARS[parts[3]]}"\n'.encode())
        elif parts[0] == "LOGOUT":
            writer.write(b"OK Goodbye\n")
            await writer.drain()
            break
        else:
            writer.write(b"ERR UNKNOWN-COMMAND\n")
        await writer.drain()
    writer.close()


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send
    def titles(s): return [(lvl, t) for lvl, t, _ in s.sent]


_files = [0]


def fresh():
    _files[0] += 1
    UM.UpsMonitor.STATE_FILE = isolate.DATA / f"ups-{_files[0]}.json"
    notes = Notes()
    return notes, UM.UpsMonitor(notes)


async def run(mon, count=1):
    for _ in range(count):
        await mon.check()


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    try:
        print("== on line power ==")
        notes, mon = fresh()
        await run(mon, 3)
        print("  OL for 3 checks ->", notes.titles())

        print("\n== an outage ==")
        VARS.update({"ups.status": "OB DISCHRG", "battery.charge": "92", "battery.runtime": "1500"})
        await run(mon)
        print("  on battery ->", notes.titles())
        print("  text:", notes.sent[0][2].replace("\n\n", " / "))
        await run(mon, 3)
        print("  three more checks -> alerts in total:", len(notes.sent))
        VARS.update({"ups.status": "OB DISCHRG LB", "battery.charge": "9", "battery.runtime": "240"})
        await run(mon, 2)
        print("  low battery ->", notes.titles()[1:])
        CLOCK[0] += 725
        VARS.update({"ups.status": "OL CHRG", "battery.charge": "30", "battery.runtime": "400"})
        await run(mon)
        print("  line power again ->", notes.titles()[2:])
        print("  text:", notes.sent[-1][2].replace("\n\n", " / "))
        await run(mon, 2)
        print("  charging on -> alerts in total:", len(notes.sent))
        print("  activity log:", [e["seconds"] for e in activity_log.since(0, "power_outage")])

        print("\n== the next outage alerts again ==")
        VARS.update({"ups.status": "OB DISCHRG LB"})
        await run(mon)
        print("  ->", notes.titles()[3:])
        VARS.update({"ups.status": "OL"}); await run(mon)

        print("\n== replace battery ==")
        notes, mon = fresh()
        VARS.update({"ups.status": "OL RB"})
        await run(mon, 3)
        print("  RB ->", notes.titles())
        VARS.update({"ups.status": "OL"}); await run(mon)
        VARS.update({"ups.status": "OL RB"}); await run(mon)
        print("  cleared, then again ->", len(notes.sent), "alerts")
        VARS.update({"ups.status": "OL"})

        print("\n== missing variables ==")
        notes, mon = fresh()
        for key in ("battery.charge", "battery.runtime", "ups.load"):
            VARS.pop(key)
        VARS["ups.status"] = "OB"
        await run(mon)
        print("  only a status ->", notes.sent[0][2].split("\n\n")[1])
        VARS.update({"battery.charge": "100", "battery.runtime": "1800", "ups.load": "23"}); VARS["ups.status"] = "OL"
        await run(mon)

        print("\n== a restart of the bot ==")
        notes, mon = fresh()
        VARS["ups.status"] = "OB"
        await run(mon)
        again = UM.UpsMonitor(notes)
        await run(again, 2)
        print("  on battery, new instance -> alerts:", len(notes.sent))
        CLOCK[0] += 60
        VARS["ups.status"] = "OL"
        await run(again)
        print("  restored after the restart ->", notes.titles()[1:])

        print("\n== an unknown UPS ==")
        os.environ["NUT_UPS"] = "nope"
        notes, mon = fresh()
        await run(mon, 3)
        print("  ->", notes.titles(), "|", notes.sent[0][2].split("\n\n")[0])
        os.environ["NUT_UPS"] = "ups"

        print("\n== the server cannot be reached ==")
        notes, mon = fresh()
        VARS["ups.status"] = "OB"
        await run(mon)
        server.close(); await server.wait_closed()
        await run(mon, 2)
        print("  2 failed checks ->", notes.titles()[1:])
        await run(mon)
        print("  3rd ->", notes.titles()[1:], "|", notes.sent[-1][2].split("\n\n")[0][:30])
        await run(mon, 3)
        print("  more failures -> alerts in total:", len(notes.sent))
        server = await asyncio.start_server(handle, "127.0.0.1", PORT)
        CLOCK[0] += 300
        VARS["ups.status"] = "OL"
        await run(mon)
        print("  back, and the outage is over ->", notes.titles()[2:])

        print("\n== the weekly summary ==")
        import modules.general.weekly_summary_monitor as WS
        week = time.time() - 3600
        activity_log._entries[:] = []
        print("  quiet week ->", WS.WeeklySummaryMonitor._power(week))
        activity_log.record("power_outage", seconds=120); activity_log.record("power_outage", seconds=725)
        print("  two outages ->", WS.WeeklySummaryMonitor._power(week))
        print("  field listed for the ups module:", ("ups", "🔌 Power", "_power") in WS.WeeklySummaryMonitor.FIELDS)

        print("\n== off ==")
        notes, mon = fresh(); ASKED.clear()
        os.environ["NUT_HOST"] = ""
        await run(mon, 3)
        print("  NUT_HOST empty -> requests:", ASKED, "| alerts:", notes.titles())
        os.environ["NUT_HOST"] = "127.0.0.1"
    finally:
        server.close()

asyncio.run(main())
