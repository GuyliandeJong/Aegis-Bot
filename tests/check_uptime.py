"""Uptime and certificate checks against local HTTP and HTTPS servers: down after two failures, back up, 4xx is up, certificates expiring, restarts."""
import asyncio
import datetime
import logging
import os
import ssl
import sys
import time
import types
from pathlib import Path

from aiohttp import web

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_COOLDOWN=0); sys.modules["config.settings"] = c
HTTP_PORT, TLS_PORT, EXPIRED_PORT, CLOSED_PORT = 18130, 18131, 18132, 18133
for name in ("UPTIME_URLS", "UPTIME_INTERVAL", "UPTIME_TIMEOUT", "UPTIME_CERT_DAYS"):
    os.environ.pop(name, None)  # the container's real .env must not change the defaults
os.environ["UPTIME_TIMEOUT"] = "1"

import modules.uptime.uptime_service as US
import modules.uptime.uptime_monitor as UM
import isolate  # state files go to a temporary folder
UM.UptimeMonitor.STATE_FILE = isolate.DATA / "uptime.json"

DAY = 86400

# Test-only certificates for 127.0.0.1, signed by a test CA (keys are
# committed on purpose, they protect nothing): current.pem expires
# 2036-01-01, renewed.pem 2037-01-01, expired.pem expired in 2021. The
# container has no `cryptography` to make them on the fly. The
# monitor's clock is moved instead of the certificates' dates.
CERTS = Path(__file__).resolve().parent / "certs"
CURRENT_EXPIRY = datetime.datetime(2036, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
RENEWED_EXPIRY = datetime.datetime(2037, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
CLOCK = [time.time()]
UM.time = types.SimpleNamespace(time=lambda: CLOCK[0])


def server_context(name):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(CERTS / f"{name}.pem"); return context


client = ssl.create_default_context(cafile=str(CERTS / "ca.pem"))
UM.UptimeMonitor.SSL_CONTEXT = client

STATUS = {"app": 200}
REQUESTS = []


async def app_page(request):
    REQUESTS.append(request.path)
    if STATUS["app"] == "slow":
        await asyncio.sleep(3)
    return web.Response(status=200 if STATUS["app"] == "slow" else STATUS["app"], text="hello")


async def login(request):
    REQUESTS.append(request.path)
    return web.Response(status=401, text="log in")


class Notes:
    def __init__(s): s.sent = []
    def __getattr__(s, level):
        async def send(title, description="", **k): s.sent.append((level, title, description))
        return send


def titles(notes):
    return [(level, title) for level, title, _ in notes.sent]


async def main():
    app = web.Application(); app.add_routes([web.get("/app", app_page), web.get("/login", login)])
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", HTTP_PORT).start()
    tls = server_context("current")
    await web.TCPSite(runner, "127.0.0.1", TLS_PORT, ssl_context=tls).start()
    await web.TCPSite(runner, "127.0.0.1", EXPIRED_PORT, ssl_context=server_context("expired")).start()
    try:
        print("== certificate levels (14 days warning, 3 critical) ==")
        t = 1_000_000.0
        for days in (30, 14.5, 13.9, 3.1, 2.9, 0.1, -1):
            print(f"  {days:5} days left -> {US.certificate_level(t + days * DAY, t, 14)}")

        print("\n== single requests ==")
        for url in (f"http://127.0.0.1:{HTTP_PORT}/app", f"http://127.0.0.1:{HTTP_PORT}/login", f"https://127.0.0.1:{TLS_PORT}/app",
                    f"https://127.0.0.1:{EXPIRED_PORT}/app", f"http://127.0.0.1:{CLOSED_PORT}/"):
            r = await US.check(url, 1, client)
            days = datetime.datetime.fromtimestamp(r["expires"], datetime.timezone.utc).date() if r["expires"] else "-"
            print(f"  {url.split('//')[1]:22} up={r['up']!s:5} status={r['status']} cert={days} reason={r['reason']!r}")
        STATUS["app"] = 500
        r = await US.check(f"http://127.0.0.1:{HTTP_PORT}/app", 1)
        print(f"  HTTP 500 -> up={r['up']} reason={r['reason']!r}")
        STATUS["app"] = "slow"
        r = await US.check(f"http://127.0.0.1:{HTTP_PORT}/app", 1)
        print(f"  slow     -> up={r['up']} reason={r['reason']!r}")
        STATUS["app"] = 200
        print("  host:", US.host("https://jellyfin.example.org/web/index.html"))

        print("\n== down and back up ==")
        os.environ["UPTIME_URLS"] = f"http://127.0.0.1:{HTTP_PORT}/app, http://127.0.0.1:{HTTP_PORT}/login"
        notes = Notes(); mon = UM.UptimeMonitor(notes)
        print("  interval:", mon.INTERVAL, "timeout:", mon.TIMEOUT)
        await mon.check(); print("  first check, both up ->", titles(notes))
        STATUS["app"] = 502
        CLOCK[0] += 300; await mon.check(); print("  one failure          ->", titles(notes))
        CLOCK[0] += 300; await mon.check(); print("  two failures         ->", titles(notes))
        CLOCK[0] += 300; await mon.check(); print("  three failures       ->", len(notes.sent), "alert(s)")
        print("  ", notes.sent[0][2].replace("\n", " / "))

        print("\n== restart while down ==")
        notes2 = Notes(); mon = UM.UptimeMonitor(notes2)
        CLOCK[0] += 300; await mon.check(); print("  still down after restart ->", titles(notes2))
        STATUS["app"] = 200
        CLOCK[0] += 300; await mon.check(); print("  answers again ->", titles(notes2))
        print("  ", notes2.sent[0][2].replace("\n", " / "))
        await mon.check(); print("  next check ->", len(notes2.sent), "alert(s) in total")

        print("\n== one failure only ==")
        notes2.sent.clear()
        STATUS["app"] = "slow"; await mon.check()
        STATUS["app"] = 200; await mon.check()
        print("  timeout once, then up ->", titles(notes2))

        print("\n== down at the first check ==")
        os.environ["UPTIME_URLS"] = f"http://127.0.0.1:{CLOSED_PORT}/"
        notes = Notes(); mon = UM.UptimeMonitor(notes)
        await mon.check(); await mon.check()
        print("  nothing listening, two checks ->", titles(notes))
        print("  removed URLs forgotten:", list(mon.state["urls"]))

        print("\n== certificates ==")
        os.environ["UPTIME_URLS"] = f"https://127.0.0.1:{TLS_PORT}/app"
        notes = Notes(); mon = UM.UptimeMonitor(notes)
        CLOCK[0] = CURRENT_EXPIRY - 90 * DAY
        await mon.check(); print("  90 days left  ->", titles(notes))
        CLOCK[0] = CURRENT_EXPIRY - 10 * DAY
        await mon.check(); print("  10 days left  ->", titles(notes))
        print("  ", notes.sent[-1][2].split("\n\n")[1].split(" (")[1])
        await mon.check(); print("  again         ->", len(notes.sent), "alert(s)")
        notes = Notes(); mon = UM.UptimeMonitor(notes)
        await mon.check(); print("  after restart ->", titles(notes))
        CLOCK[0] = CURRENT_EXPIRY - 2 * DAY
        await mon.check(); print("  2 days left   ->", titles(notes))
        await mon.check(); print("  again         ->", len(notes.sent), "alert(s)")
        tls.load_cert_chain(CERTS / "renewed.pem")
        await mon.check(); print("  renewed       ->", len(notes.sent), "alert(s), state", mon.state["urls"][os.environ["UPTIME_URLS"]]["certificate"])
        CLOCK[0] = RENEWED_EXPIRY - 10 * DAY
        await mon.check(); print("  expiring again ->", titles(notes)[-1])

        print("\n== expired certificate ==")
        os.environ["UPTIME_URLS"] = f"https://127.0.0.1:{EXPIRED_PORT}/app"
        notes = Notes(); mon = UM.UptimeMonitor(notes)
        await mon.check(); await mon.check()
        print("  ", titles(notes), "|", notes.sent[0][2].split("\n\n")[1].replace("\n", ": "))

        print("\n== not configured ==")
        os.environ["UPTIME_URLS"] = " "
        REQUESTS.clear(); notes = Notes(); mon = UM.UptimeMonitor(notes)
        await mon.check()
        print("  UPTIME_URLS empty -> requests:", REQUESTS, "alerts:", notes.sent)
    finally:
        await runner.cleanup()

asyncio.run(main())
