"""
Walk iDRAC's Redfish pages and list everything that looks power related.

Run inside the container:

    docker exec aegis-bot python scripts/redfish_explore.py

Starts at /redfish/v1, follows the links between pages, and prints each
value whose name mentions power, watts, volts, amps or a power supply,
with the page it is on. Logs, schemas, accounts and network settings
are skipped, and identifiers such as serial numbers, MAC and IP
addresses are never printed, so the output can be shared.

An optional word narrows or widens the search, e.g. "fan" or "temp":

    docker exec aegis-bot python scripts/redfish_explore.py fan
"""

import asyncio
import re
import sys

# Run from /app, so the bot's own modules are found.
sys.path.insert(0, ".")

from modules.idrac.redfish_client import RedfishClient


MAX_PAGES = 400
PARALLEL = 4

DEFAULT_PATTERN = r"power|watt|volt|amp|psu|supply|supplies|consum|energy|current"

# Pages that are large, private or unrelated.
SKIP = re.compile(
    r"LogServices|/Entries|JsonSchemas|Registries|AccountService|"
    r"SessionService|EventService|CertificateService|UpdateService|"
    r"NetworkProtocol|EthernetInterfaces|NetworkAdapters|NetworkInterfaces|"
    r"Jobs|JobService|TaskService|Assembly|Bios|SecureBoot|VirtualMedia|"
    r"/Members/|DellJobService|DellLCService|SerialInterfaces",
    re.IGNORECASE,
)

# Never printed, whatever their value: any part of the key path whose
# name contains one of these.
PRIVATE = re.compile(
    r"Serial|PartNumber|ServiceTag|AssetTag|SKU|UUID|PPID|MAC|IPv4|IPv6|"
    r"IPAddress|Address|HostName|FQDN|DNS|Password|UserName|Email|"
    r"Location|Contact",
)


def private(key: str) -> bool:
    return any(
        PRIVATE.search(part.split("[")[0])
        for part in key.split(".")
    )


def walk(value, path=""):
    """Yield (key path, value) for every scalar in a JSON document."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, f"{path}[{index}]")
    else:
        yield path, value


def links(document):
    """Every Redfish page this one links to."""
    for key, value in walk(document):
        if key.endswith("@odata.id") and isinstance(value, str):
            yield value.split("#")[0]


async def main(pattern: str) -> None:
    wanted = re.compile(pattern, re.IGNORECASE)
    client = RedfishClient()
    seen = {"/redfish/v1"}
    queue = ["/redfish/v1"]
    pages = 0
    found = 0

    async def fetch(page):
        endpoint = page.removeprefix("/redfish/v1")
        try:
            return page, await client.get(endpoint)
        except Exception as ex:
            return page, ex

    try:
        while queue and pages < MAX_PAGES:
            batch, queue = queue[:PARALLEL], queue[PARALLEL:]
            results = await asyncio.gather(*(fetch(p) for p in batch))

            for page, document in results:
                pages += 1

                if isinstance(document, Exception):
                    continue

                hits = []

                # A page about something power related (a sensor page
                # such as "System Board Pwr Consumption") is shown in
                # full, so its reading and unit come along.
                whole_page = bool(
                    wanted.search(str(document.get("Name") or ""))
                    or wanted.search(str(document.get("Id") or ""))
                )

                for key, value in walk(document):
                    if "@odata" in key or private(key):
                        continue

                    name = key.split(".")[-1]
                    text = str(value)

                    if whole_page or wanted.search(key) or (
                        name == "Name" and wanted.search(text)
                    ):
                        hits.append(f"  {key} = {text[:80]}")

                if hits:
                    found += len(hits)
                    print(page)
                    print("\n".join(hits[:40]))

                    if len(hits) > 40:
                        print(f"  … {len(hits) - 40} more")

                for link in links(document):
                    if (
                        link.startswith("/redfish/v1")
                        and link not in seen
                        and not SKIP.search(link)
                    ):
                        seen.add(link)
                        queue.append(link)
    finally:
        await client.close()

    print(
        f"\n{pages} page(s) read, {found} matching value(s)"
        + (f", stopped at {MAX_PAGES} pages" if queue else "")
        + "."
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATTERN))
