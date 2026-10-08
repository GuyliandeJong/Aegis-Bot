"""
Read datastores and virtual machines from a VMware ESXi host.

Standalone ESXi has no REST API, only the vSphere SOAP API at
https://<host>/sdk. This speaks the four calls needed with aiohttp, so
no extra library is required:

    RetrieveServiceContent  where the managers are
    Login                   session cookie
    CreateContainerView     every Datastore / VirtualMachine
    RetrievePropertiesEx    their properties

.env:
    ESXI_HOST       address of the ESXi host
    ESXI_USERNAME   preferably a user with the read-only role
    ESXI_PASSWORD
"""

import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import aiohttp

from utils.logger import logger


NS = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "vim": "urn:vim25",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

DATASTORE_PROPERTIES = (
    "summary.name",
    "summary.capacity",
    "summary.freeSpace",
    "summary.accessible",
)

VM_PROPERTIES = (
    "name",
    "runtime.powerState",
)


def configured() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in ("ESXI_HOST", "ESXI_USERNAME", "ESXI_PASSWORD")
    )


def _envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns="urn:vim25">'
        f"<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
    )


def _moref(tag: str, ref: dict) -> str:
    return f'<{tag} type="{escape(ref["type"])}">{escape(ref["value"])}</{tag}>'


def _ref(element) -> dict:
    return {
        "type": element.get("type", ""),
        "value": (element.text or "").strip(),
    }


def _value(element):
    """Convert one <val> to a Python value by its xsi:type."""
    kind = element.get(f"{{{NS['xsi']}}}type", "")
    text = (element.text or "").strip()

    if kind.endswith("long") or kind.endswith("int"):
        try:
            return int(text)
        except ValueError:
            return None

    if kind.endswith("boolean"):
        return text.lower() == "true"

    return text


class EsxiError(Exception):
    """The ESXi host refused a request; the message is safe to show."""


class EsxiService:
    def __init__(self):
        host = os.getenv("ESXI_HOST", "").strip().rstrip("/")
        host = host.removeprefix("https://").removeprefix("http://")
        self.url = f"https://{host}/sdk"
        self.username = os.getenv("ESXI_USERNAME", "").strip()
        self.password = os.getenv("ESXI_PASSWORD", "")
        self.session: aiohttp.ClientSession | None = None
        self.content: dict = {}

    async def _call(self, action: str, body: str) -> ET.Element:
        async with self.session.post(
            self.url,
            data=_envelope(body).encode(),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f"urn:vim25/{action}",
            },
            # ESXi uses a self-signed certificate, like iDRAC.
            ssl=False,
        ) as response:
            text = await response.text()

        root = ET.fromstring(text)
        fault = root.find(".//soap:Fault/faultstring", NS)

        if fault is not None:
            raise EsxiError((fault.text or "SOAP fault").strip()[:200])

        body_element = root.find("soap:Body", NS)

        if body_element is None or not len(body_element):
            raise EsxiError(f"Empty answer to {action}.")

        return body_element[0]

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        )

        response = await self._call(
            "RetrieveServiceContent",
            '<RetrieveServiceContent>'
            '<_this type="ServiceInstance">ServiceInstance</_this>'
            '</RetrieveServiceContent>',
        )

        returnval = response.find("vim:returnval", NS)

        for name in ("rootFolder", "propertyCollector", "viewManager", "sessionManager"):
            element = returnval.find(f"vim:{name}", NS) if returnval is not None else None

            if element is None:
                raise EsxiError(f"The host did not report its {name}.")

            self.content[name] = _ref(element)

        await self._call(
            "Login",
            "<Login>"
            + _moref("_this", self.content["sessionManager"])
            + f"<userName>{escape(self.username)}</userName>"
            + f"<password>{escape(self.password)}</password>"
            + "</Login>",
        )

    async def close(self) -> None:
        if self.session is None:
            return

        try:
            if "sessionManager" in self.content:
                await self._call(
                    "Logout",
                    "<Logout>" + _moref("_this", self.content["sessionManager"]) + "</Logout>",
                )
        except Exception as ex:
            logger.debug("[ESXi] Logout failed: %s", ex)
        finally:
            await self.session.close()
            self.session = None

    async def _objects(self, kind: str, properties) -> list[dict]:
        """Every object of one type, with the requested properties."""
        view_response = await self._call(
            "CreateContainerView",
            "<CreateContainerView>"
            + _moref("_this", self.content["viewManager"])
            + _moref("container", self.content["rootFolder"])
            + f"<type>{kind}</type>"
            + "<recursive>true</recursive>"
            + "</CreateContainerView>",
        )
        view = _ref(view_response.find("vim:returnval", NS))

        try:
            paths = "".join(f"<pathSet>{p}</pathSet>" for p in properties)
            response = await self._call(
                "RetrievePropertiesEx",
                "<RetrievePropertiesEx>"
                + _moref("_this", self.content["propertyCollector"])
                + "<specSet>"
                + f"<propSet><type>{kind}</type>{paths}</propSet>"
                + "<objectSet>"
                + _moref("obj", view)
                + "<skip>true</skip>"
                + '<selectSet xsi:type="TraversalSpec">'
                + "<name>view</name><type>ContainerView</type>"
                + "<path>view</path><skip>false</skip>"
                + "</selectSet>"
                + "</objectSet>"
                + "</specSet>"
                + "<options/>"
                + "</RetrievePropertiesEx>",
            )
        finally:
            try:
                await self._call(
                    "DestroyView",
                    "<DestroyView>" + _moref("_this", view) + "</DestroyView>",
                )
            except Exception as ex:
                logger.debug("[ESXi] DestroyView failed: %s", ex)

        result = []

        for obj in response.findall(".//vim:objects", NS):
            item = {}

            for prop in obj.findall("vim:propSet", NS):
                name = prop.find("vim:name", NS)
                val = prop.find("vim:val", NS)

                if name is not None and val is not None:
                    item[name.text] = _value(val)

            result.append(item)

        return result

    async def get_status(self) -> dict:
        """
        Return {"datastores": [...], "vms": [...]}.

        Datastore: name, capacity, free (bytes), accessible.
        VM: name, power ("poweredOn", "poweredOff" or "suspended").
        """
        datastores = [
            {
                "name": d.get("summary.name") or "Unnamed datastore",
                "capacity": d.get("summary.capacity") or 0,
                "free": d.get("summary.freeSpace") or 0,
                "accessible": d.get("summary.accessible", True) is not False,
            }
            for d in await self._objects("Datastore", DATASTORE_PROPERTIES)
        ]

        vms = [
            {
                "name": v.get("name") or "Unnamed VM",
                "power": v.get("runtime.powerState") or "unknown",
            }
            for v in await self._objects("VirtualMachine", VM_PROPERTIES)
        ]

        datastores.sort(key=lambda d: d["name"].lower())
        vms.sort(key=lambda v: v["name"].lower())

        return {"datastores": datastores, "vms": vms}


async def fetch_status() -> dict:
    """Connect, read, and always log out."""
    service = EsxiService()

    try:
        await service.connect()
        return await service.get_status()
    finally:
        await service.close()
