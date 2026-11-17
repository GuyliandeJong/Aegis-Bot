"""Health endpoint on a local port: 200 while connected and every monitor reports, 503 degraded or down otherwise, counts only, no other path, off when unset, a busy port tried again, and a second check not starting a second server."""
import asyncio
import logging
import os
import socket
import sys
import time
import types

import aiohttp

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10, ALERT_COOLDOWN=0, MONITOR_TIMEOUT=1, MONITOR_INTERVAL=60); sys.modules["config.settings"] = c
PORT = 18201
for key in ("HEALTH_PORT", "HEALTH_HOST"):
    os.environ.pop(key, None)

from core import monitor_manager as MM
from modules.general.health_endpoint import HealthEndpoint
import isolate  # state files go to a temporary folder


class Bot:
    def __init__(self): self.closed = False; self.latency = 0.05
    def is_closed(self): return self.closed


async def get(path="/health", method="GET"):
    async with aiohttp.ClientSession() as session:
        async with session.request(method, f"http://127.0.0.1:{PORT}{path}") as response:
            return response.status, (await response.json() if response.content_type == "application/json" else await response.text())


async def main():
    bot = Bot()
    endpoint = HealthEndpoint(types.SimpleNamespace(bot=bot))

    print("== off ==")
    await endpoint.check()
    print("  HEALTH_PORT unset -> server started:", endpoint.runner is not None)
    os.environ["HEALTH_PORT"] = "0"
    await endpoint.check()
    print("  HEALTH_PORT=0     -> server started:", endpoint.runner is not None)
    os.environ["HEALTH_PORT"] = "abc"
    print("  not a number      ->", HealthEndpoint.port())

    print("\n== answers ==")
    os.environ["HEALTH_PORT"] = str(PORT)
    MM.HEALTH.clear()
    try:
        await endpoint.check()
        first = endpoint.runner
        await endpoint.check()
        print("  started, and a second check keeps the same server:", first is endpoint.runner)
        print("  connected, no monitor seen yet ->", await get())

        MM.record_health("GoodMonitor", 30)
        print("  one good monitor               ->", await get())

        MM.record_health("SecretNameMonitor", 30, "boom with a secret")
        status, body = await get()
        print("  a failing monitor              ->", status, body, "| name or message shown:", "Secret" in str(body) or "boom" in str(body))

        MM.HEALTH.pop("SecretNameMonitor")
        MM.HEALTH["GoodMonitor"]["last_ok"] = time.time() - 900
        print("  a silent monitor               ->", await get())
        MM.HEALTH["GoodMonitor"]["last_ok"] = time.time()

        bot.closed = True
        print("  Discord connection closed      ->", await get())
        bot.closed = False; bot.latency = float("inf")
        print("  latency not finite             ->", await get())
        bot.latency = 0.05

        print("\n== other requests ==")
        print("  /            ->", (await get("/"))[0])
        print("  /metrics     ->", (await get("/metrics"))[0])
        print("  POST /health ->", (await get("/health", "POST"))[0])
        print("  HEAD /health ->", (await get("/health", "HEAD"))[0])
    finally:
        if endpoint.runner:
            await endpoint.runner.cleanup()

    print("\n== a busy port ==")
    blocker = socket.socket(); blocker.bind(("127.0.0.1", PORT)); blocker.listen()
    os.environ["HEALTH_HOST"] = "127.0.0.1"
    busy = HealthEndpoint(types.SimpleNamespace(bot=bot))
    await busy.check()
    print("  port in use -> server started:", busy.runner is not None)
    blocker.close()
    await busy.check()
    print("  next check, port free -> server started:", busy.runner is not None, "|", (await get())[0])
    await busy.runner.cleanup()

asyncio.run(main())
