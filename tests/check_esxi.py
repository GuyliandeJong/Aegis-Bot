"""ESXi over the vSphere SOAP API against a local fake host: login, datastores, VMs, logout, and the monitor's alerts."""
import asyncio
import logging
import os
import sys
import types

from aiohttp import web

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
PORT = 18114
os.environ.update({"ESXI_HOST": f"127.0.0.1:{PORT}", "ESXI_USERNAME": "monitor", "ESXI_PASSWORD": "p<&>w", "ESXI_IGNORE_VMS": "Scratch"})

import services.esxi_service as ES
import monitors.esxi_monitor as EM
import isolate  # state files go to a temporary folder
EM.EsxiMonitor.STATE_FILE = isolate.DATA / "esxi.json"

_init = ES.EsxiService.__init__
def init(self): _init(self); self.url = self.url.replace("https://", "http://")
ES.EsxiService.__init__ = init

GIB = 1024 ** 3
DATASTORES = {"datastore-1": ["datastore1", 500 * GIB, 300 * GIB, True], "datastore-2": ["media", 4000 * GIB, 1000 * GIB, True]}
VMS = {"vm-1": ["docker-host", "poweredOn"], "vm-2": ["homeassistant", "poweredOn"], "vm-3": ["Scratch", "poweredOn"]}
CALLS = []
LOGINS = []

NS = 'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"'


def envelope(body):
    return f'<?xml version="1.0"?><soapenv:Envelope {NS}><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>'


def prop(name, value, kind):
    return f'<propSet><name>{name}</name><val xsi:type="{kind}">{value}</val></propSet>'


async def sdk(request):
    action = request.headers.get("SOAPAction", "").split("/")[-1]
    body = await request.text()
    CALLS.append(action)
    if action == "RetrieveServiceContent":
        out = ('<RetrieveServiceContentResponse xmlns="urn:vim25"><returnval>'
               '<rootFolder type="Folder">ha-folder-root</rootFolder><propertyCollector type="PropertyCollector">ha-property-collector</propertyCollector>'
               '<viewManager type="ViewManager">ViewManager</viewManager><sessionManager type="SessionManager">ha-sessionmgr</sessionManager>'
               '</returnval></RetrieveServiceContentResponse>')
    elif action == "Login":
        LOGINS.append(body)
        if "<password>p&lt;&amp;&gt;w</password>" not in body:
            return web.Response(text=envelope('<soapenv:Fault><faultcode>ServerFaultCode</faultcode><faultstring>Cannot complete login due to an incorrect user name or password.</faultstring></soapenv:Fault>'), status=500)
        resp = web.Response(text=envelope('<LoginResponse xmlns="urn:vim25"><returnval><key>s</key></returnval></LoginResponse>'))
        resp.set_cookie("vmware_soap_session", "abc")
        return resp
    elif action == "CreateContainerView":
        kind = "Datastore" if "<type>Datastore</type>" in body else "VirtualMachine"
        out = f'<CreateContainerViewResponse xmlns="urn:vim25"><returnval type="ContainerView">view-{kind}</returnval></CreateContainerViewResponse>'
    elif action == "RetrievePropertiesEx":
        assert "vmware_soap_session=abc" in request.headers.get("Cookie", ""), "not logged in"
        objects = ""
        if "<type>Datastore</type>" in body:
            for ref, (name, cap, free, ok) in DATASTORES.items():
                objects += (f'<objects><obj type="Datastore">{ref}</obj>' + prop("summary.accessible", str(ok).lower(), "xsd:boolean")
                            + prop("summary.capacity", cap, "xsd:long") + prop("summary.freeSpace", free, "xsd:long")
                            + prop("summary.name", name, "xsd:string") + "</objects>")
        else:
            for ref, (name, power) in VMS.items():
                objects += (f'<objects><obj type="VirtualMachine">{ref}</obj>' + prop("name", name, "xsd:string")
                            + prop("runtime.powerState", power, "VirtualMachinePowerState") + "</objects>")
        out = f'<RetrievePropertiesExResponse xmlns="urn:vim25"><returnval>{objects}</returnval></RetrievePropertiesExResponse>'
    elif action in ("DestroyView", "Logout"):
        out = f'<{action}Response xmlns="urn:vim25"></{action}Response>'
    else:
        out = ""
    return web.Response(text=envelope(out))


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title))
        return send


async def main():
    app = web.Application(); app.add_routes([web.post("/sdk", sdk)])
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "127.0.0.1", PORT).start()
    try:
        print("== reading the host ==")
        status = await ES.fetch_status()
        for d in status["datastores"]:
            print(f"  datastore {d['name']:10} capacity={d['capacity'] // GIB} GiB free={d['free'] // GIB} GiB accessible={d['accessible']}")
        for v in status["vms"]:
            print(f"  vm {v['name']:14} {v['power']}")
        print("  calls:", CALLS)
        print("  password sent escaped:", "<password>p&lt;&amp;&gt;w</password>" in LOGINS[0])

        print("\n== monitor ==")
        notes = Notes(); mon = EM.EsxiMonitor(notes)
        await mon.check(); print("first check, alerts:", notes.sent)
        DATASTORES["datastore-2"][2] = 400 * GIB    # 90% used
        VMS["vm-2"][1] = "poweredOff"
        VMS["vm-3"][1] = "poweredOff"               # ignored
        await mon.check()
        DATASTORES["datastore-2"][2] = 100 * GIB    # 97.5% used
        DATASTORES["datastore-1"][3] = False
        VMS["vm-2"][1] = "poweredOn"
        await mon.check()
        DATASTORES["datastore-2"][2] = 2000 * GIB
        DATASTORES["datastore-1"][3] = True
        await mon.check()
        for level, title in notes.sent:
            print(f"  {level:9} {title}")

        print("\n== host down ==")
        notes.sent.clear()
        os.environ["ESXI_PASSWORD"] = "wrong"
        for _ in range(4):
            await mon.check()
        os.environ["ESXI_PASSWORD"] = "p<&>w"
        await mon.check()
        print("  alerts:", notes.sent)

        del os.environ["ESXI_HOST"]
        CALLS.clear(); await mon.check()
        print("  not configured -> requests:", CALLS)
    finally:
        await runner.cleanup()

asyncio.run(main())
