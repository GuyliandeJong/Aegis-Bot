"""iDRAC System Event Log against a local fake Redfish server: paging, the first check, grouped alerts per severity, OK entries, 404 and the off switch."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
PORT = 18155
c = types.ModuleType("config.settings")
c.settings = types.SimpleNamespace(IDRAC_HOST=f"127.0.0.1:{PORT}", IDRAC_USERNAME="root", IDRAC_PASSWORD="calvin")
sys.modules["config.settings"] = c
# Inside the bot's container the real .env is loaded; its values must
# not change what this check expects.
for name in ("IDRAC_EVENT_LOG", "IDRAC_EVENT_LOG_ALL"):
    os.environ.pop(name, None)

import modules.idrac.redfish_client as RC
import modules.idrac.hardware_service as HS
import modules.idrac.event_log_monitor as EM
import isolate  # state files go to a temporary folder
EM.EventLogMonitor.STATE_FILE = isolate.DATA / "event_log.json"

_init = RC.RedfishClient.__init__
def init(self): _init(self); self.base_url = self.base_url.replace("https://", "http://")
RC.RedfishClient.__init__ = init

PATH = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries"
START = datetime(2026, 10, 1, 8, 0, tzinfo=timezone(timedelta(hours=-5)))
ENTRIES = []   # oldest first; served newest first like iDRAC
REQUESTS = []
MISSING = {"sel": False}


def add(severity, message, sensor="Fan", minutes=None, created=None):
    number = len(ENTRIES) + 1
    when = created or (START + timedelta(minutes=minutes if minutes is not None else number))
    ENTRIES.append({"@odata.id": f"{PATH}/{number}", "@odata.type": "#LogEntry.v1_15_0.LogEntry", "Id": str(number),
                    "Created": when.isoformat(), "EntryType": "SEL", "Severity": severity, "Message": message,
                    "MessageId": "0x00000000", "SensorType": sensor, "SensorNumber": 48})
    return when


async def entries(request):
    REQUESTS.append(request.path_qs)
    if MISSING["sel"]:
        return web.json_response({"error": {"code": "Base.1.12.GeneralError", "message": "Resource not found"}}, status=404)
    if request.headers.get("Authorization", "") != "Basic cm9vdDpjYWx2aW4=":
        return web.json_response({}, status=401)
    skip = int(request.query.get("$skip", 0))
    members = list(reversed(ENTRIES))
    page = {"@odata.id": PATH, "Name": "Log Entry Collection", "Members": members[skip:skip + 50], "Members@odata.count": len(members)}
    if skip + 50 < len(members):
        page["Members@odata.nextLink"] = f"{PATH}?$skip={skip + 50}"
    return web.json_response(page)


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


async def main():
    app = web.Application(); app.router.add_get(PATH, entries)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()

    print("== reading the log ==")
    for n in range(60):
        add("OK" if n % 3 else "Warning", f"Old event {n + 1}.", sensor="Temperature")
    read = await HS.HardwareService().get_event_log()
    print("entries read:", len(read), "| requests:", REQUESTS)
    newest = max(read, key=lambda e: e["created"])
    print("newest:", newest["id"], newest["severity"], newest["sensor"], newest["created"].isoformat())
    odd = HS.log_entries([{"Id": "x", "Created": "2026-10-01T08:00:00Z", "Severity": "Informational", "Message": "a\n  b"},
                          {"Id": "y", "Created": "not a date"}, "junk"])
    print("odd entries:", [(e["id"], e["severity"], e["message"], e["created"].isoformat()) for e in odd])

    print("\n== monitor ==")
    notes = Notes(); mon = EM.EventLogMonitor(notes)
    REQUESTS.clear()
    await mon.check(); print("first check, alerts:", len(notes.sent), "| initialized:", mon.state["initialized"])
    await mon.check(); print("nothing new, alerts:", len(notes.sent))

    when = add("Critical", "The system board fan1 RPM is less than the lower critical threshold.", sensor="Fan")
    add("Warning", "The system inlet temperature is greater than the upper warning threshold.", sensor="Temperature")
    add("Warning", "The power supply redundancy is lost.", sensor="Power Supply / Converter")
    add("OK", "The system board fan1 RPM is within range.", sensor="Fan")
    await mon.check()
    for level, title, description in notes.sent:
        print(f"  {level:9} {title} | lines: {description.count('• ')}")
    print("critical text:", notes.sent[0][2].replace(chr(10), " / "))
    print("time tag:", f"<t:{int(when.timestamp())}:f>" in notes.sent[0][2])
    await mon.check(); print("same entries again -> alerts:", len(notes.sent))

    # Same second as the newest entry, but a new Id.
    last = ENTRIES[-1]["Created"]
    add("Warning", "Correctable memory error rate exceeded for DIMM A1.", sensor="Memory", created=datetime.fromisoformat(last))
    await mon.check(); print("same second, new Id ->", notes.sent[-1][:2])

    os.environ["IDRAC_EVENT_LOG_ALL"] = "true"
    add("OK", "Log cleared.", sensor="Event Logging Disabled")
    await mon.check(); print("IDRAC_EVENT_LOG_ALL=true, OK entry ->", notes.sent[-1][:2])
    os.environ.pop("IDRAC_EVENT_LOG_ALL")

    for n in range(13):
        add("Warning", f"Drive {n} is predicted to fail.", sensor="Drive Slot / Bay")
    await mon.check()
    level, title, description = notes.sent[-1]
    print("13 warnings at once ->", level, title, "| lines:", description.count("• "), "|", description.splitlines()[-1])
    print("alerts in total:", len(notes.sent))

    restarted = EM.EventLogMonitor(Notes())
    await restarted.check(); print("after a restart, alerts:", len(restarted.notification_service.sent))

    print("\n== off switch and missing log ==")
    REQUESTS.clear()
    os.environ["IDRAC_EVENT_LOG"] = "false"
    await mon.check(); print("IDRAC_EVENT_LOG=false -> requests:", REQUESTS)
    os.environ.pop("IDRAC_EVENT_LOG")

    MISSING["sel"] = True
    gone = Notes(); mon404 = EM.EventLogMonitor(gone)
    await mon404.check(); await mon404.check(); await mon404.check()
    print("404 -> requests:", len(REQUESTS), "| alerts:", gone.sent, "| stopped:", mon404.unavailable)

    await runner.cleanup()

asyncio.run(main())
