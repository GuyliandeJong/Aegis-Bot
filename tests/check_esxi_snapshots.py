"""ESXi snapshots against a local fake host: the snapshot tree is flattened, ages are read, old ones are warned about once a day, removal is reported once."""
import asyncio
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18160
os.environ.update({"ESXI_HOST": f"127.0.0.1:{PORT}", "ESXI_USERNAME": "monitor", "ESXI_PASSWORD": "p", "ESXI_IGNORE_VMS": "lab"})
# The real .env inside the bot's container must not change the default (7 days).
os.environ.pop("ESXI_SNAPSHOT_DAYS", None)

import modules.esxi.esxi_service as ES
import modules.esxi.esxi_monitor as EM
import isolate  # state files go to a temporary folder
EM.EsxiMonitor.STATE_FILE = isolate.DATA / "esxi-snapshots.json"

_init = ES.EsxiService.__init__
def init(self): _init(self); self.url = self.url.replace("https://", "http://")
ES.EsxiService.__init__ = init

NOW = datetime.now(timezone.utc)
def iso(days, style="Z"):
    moment = NOW - timedelta(days=days, minutes=5)
    if style == "Z":
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if style == "offset":   # same moment, written in +02:00
        return moment.astimezone(timezone(timedelta(hours=2))).isoformat(timespec="seconds")
    return style            # literal text

# VM ref -> name, and its snapshot trees: (name, createTime, children).
# "web" has two roots; the first one has a chain of children.
VMS = {
    "1": ["web", [("before-upgrade", iso(12), [("after-upgrade", iso(9, "offset"), [("tuning & tests", iso(2), [])])]),
                  ("unreadable", iso(0, "yesterday"), [])]],
    "2": ["db", [("fresh", iso(0.2), [])]],
    "3": ["lab", [("ancient", iso(300), [])]],     # in ESXI_IGNORE_VMS
    "4": ["mail", []],                             # no snapshots: property left out
}

NS = 'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"'


def envelope(body):
    return f'<?xml version="1.0"?><soapenv:Envelope {NS}><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>'


def trees(ref, items, tag="rootSnapshotList"):
    """VirtualMachineSnapshotTree as in the vSphere API (pyVmomi _typeinfo_vim): arrays are repeated elements."""
    out = ""
    for i, (name, created, children) in enumerate(items):
        out += (f'<{tag}><snapshot type="VirtualMachineSnapshot">{ref}-snapshot-{i}</snapshot><vm type="VirtualMachine">{ref}</vm>'
                f'<name>{name.replace("&", "&amp;")}</name><description>made by hand</description><id>{i + 1}</id>'
                f'<createTime>{created}</createTime><state>poweredOn</state><quiesced>false</quiesced>'
                + trees(ref, children, "childSnapshotList")
                + f'<replaySupported>false</replaySupported></{tag}>')
    return out


async def sdk(request):
    action = request.headers.get("SOAPAction", "").split("/")[-1]
    body = await request.text()
    if action == "RetrieveServiceContent":
        out = ('<RetrieveServiceContentResponse xmlns="urn:vim25"><returnval>'
               '<rootFolder type="Folder">ha-folder-root</rootFolder><propertyCollector type="PropertyCollector">ha-property-collector</propertyCollector>'
               '<viewManager type="ViewManager">ViewManager</viewManager><sessionManager type="SessionManager">ha-sessionmgr</sessionManager>'
               '</returnval></RetrieveServiceContentResponse>')
    elif action == "Login":
        out = '<LoginResponse xmlns="urn:vim25"><returnval><key>s</key></returnval></LoginResponse>'
    elif action == "CreateContainerView":
        out = '<CreateContainerViewResponse xmlns="urn:vim25"><returnval type="ContainerView">view</returnval></CreateContainerViewResponse>'
    elif action == "RetrievePropertiesEx":
        objects = ""
        if "<type>VirtualMachine</type>" in body:
            assert "<pathSet>snapshot</pathSet>" in body, "snapshot not requested"
            for ref, (name, snaps) in VMS.items():
                objects += (f'<objects><obj type="VirtualMachine">{ref}</obj>'
                            f'<propSet><name>name</name><val xsi:type="xsd:string">{name}</val></propSet>'
                            '<propSet><name>runtime.powerState</name><val xsi:type="VirtualMachinePowerState">poweredOn</val></propSet>')
                if snaps:
                    objects += ('<propSet><name>snapshot</name><val xsi:type="VirtualMachineSnapshotInfo">'
                                f'<currentSnapshot type="VirtualMachineSnapshot">{ref}-snapshot-0</currentSnapshot>'
                                + trees(ref, snaps) + '</val></propSet>')
                objects += "</objects>"
        out = f'<RetrievePropertiesExResponse xmlns="urn:vim25"><returnval>{objects}</returnval></RetrievePropertiesExResponse>'
    else:
        out = f'<{action}Response xmlns="urn:vim25"></{action}Response>'
    return web.Response(text=envelope(out))


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


async def main():
    app = web.Application(); app.add_routes([web.post("/sdk", sdk)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== reading the host ==")
        status = await ES.fetch_status()
        for v in status["vms"]:
            print(f"  {v['name']:5}", [(s["name"], EM.EsxiMonitor.age(s["created"])) for s in v["snapshots"]])
        print("  description kept:", status["vms"][3]["snapshots"][0]["description"])

        print("\n== monitor ==")
        notes = Notes(); mon = EM.EsxiMonitor(notes)
        await mon.check()
        for level, title, description in notes.sent:
            print(f"  {level:9} {title} | {' / '.join(description.splitlines()[:3])}")
        notes.sent.clear(); await mon.check()
        print("  same day ->", notes.sent)
        mon.state["snapshots"]["web"] -= 86400        # a day later
        await mon.check()
        print("  a day later ->", [(l, t) for l, t, d in notes.sent])
        notes.sent.clear()
        VMS["1"][1][0] = ("after-upgrade", iso(2), [])   # the old one is deleted
        await mon.check(); await mon.check()
        print("  old one deleted, two checks ->", [(l, t) for l, t, d in notes.sent])

        print("\n== /esxi ==")
        VMS["1"][1][0] = ("before-upgrade", iso(12), [])
        import modules.esxi.commands.esxi as EC
        sent = []
        async def defer(**k): pass
        async def send(*a, **k): sent.append(k["embed"])
        interaction = types.SimpleNamespace(
            user=types.SimpleNamespace(display_name="t", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png")),
            response=types.SimpleNamespace(defer=defer), followup=types.SimpleNamespace(send=send))
        await EC.Esxi(None).esxi.callback(EC.Esxi(None), interaction)
        for line in sent[-1].fields[2].value.splitlines():
            print("  ", line)

        print("\n== turned off ==")
        os.environ["ESXI_SNAPSHOT_DAYS"] = "0"
        notes.sent.clear(); mon.state["snapshots"].clear()
        await mon.check()
        print("  ESXI_SNAPSHOT_DAYS=0 ->", notes.sent, mon.state["snapshots"])
    finally:
        await runner.cleanup()

asyncio.run(main())
