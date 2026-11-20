"""/bot update: off until REMOTE_UPDATE is set, only allowed users, only valid branch names, a confirmation only the same person can press, the request file written on confirm and nothing written on cancel, the activity log, and an unwritable data folder."""
import asyncio
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
for key in [k for k in os.environ if k.startswith(("PORTAINER_", "REMOTE_UPDATE"))]:
    del os.environ[key]
os.environ["PORTAINER_ALLOWED_USER_IDS"] = "42"

from core import activity_log
import modules.containers.commands.bot as BC
from modules.containers import update_service as US
import isolate  # state files go to a temporary folder

FOLDER = Path(tempfile.mkdtemp(prefix="update-data-", dir=str(isolate.DATA)))
US.REQUEST_FILE = FOLDER / "update_request"


class Interaction:
    def __init__(self, uid=42):
        self.user = types.SimpleNamespace(id=uid, display_name="t", display_avatar=types.SimpleNamespace(url="https://cdn.example/a.png"))
        self.log = []
        self.response = types.SimpleNamespace(send_message=self._send, edit_message=self._edit)
    async def _send(self, content=None, **k): self.log.append(("send", content, k))
    async def _edit(self, **k): self.log.append(("edit", k.get("content"), k))
    async def edit_original_response(self, **k): self.log.append(("original", None, k))


def request_text(): return US.REQUEST_FILE.read_text() if US.REQUEST_FILE.exists() else None


async def main():
    cog = BC.BotCog(types.SimpleNamespace())

    print("== the command ==")
    print("  administrators only by default:", BC.BotCog.control_group.default_permissions.administrator)
    print("  subcommands:", sorted(c.name for c in BC.BotCog.control_group.commands))

    print("\n== off by default ==")
    i = Interaction(); await cog.update.callback(cog, i, "main")
    print("  REMOTE_UPDATE unset ->", i.log[0][1][:40], "| buttons:", "view" in i.log[0][2], "| file:", request_text())
    os.environ["REMOTE_UPDATE"] = "no"
    print("  REMOTE_UPDATE=no ->", US.enabled())

    os.environ["REMOTE_UPDATE"] = "true"
    print("\n== who and what ==")
    i = Interaction(uid=7); await cog.update.callback(cog, i, "main")
    print("  not in PORTAINER_ALLOWED_USER_IDS ->", i.log[0][1], "| buttons:", "view" in i.log[0][2])
    for name in ("--upload-pack=x", "../x", "a b", "$(id)", "x" * 200, ""):
        i = Interaction(); await cog.update.callback(cog, i, name)
        print(f"  {name[:24]!r:28} -> refused as not a valid branch name:", "is not a valid branch name" in i.log[0][1] and "view" not in i.log[0][2])
    print("  valid_branch:", [US.valid_branch(n) for n in ("main", "feature/ops-additions", "v1.2.0", "a..b", "-x", "a/", "x.lock")])

    print("\n== the confirmation ==")
    i = Interaction(); await cog.update.callback(cog, i, " fix/temperature-alert-layout ")
    k = i.log[0][2]
    print("  question:", k["embed"].title.split(" ", 1)[1], "| private:", k["ephemeral"], "| buttons:", [b.label for b in k["view"].children])
    view = k["view"]
    print("  someone else may press:", await view.interaction_check(Interaction(uid=7)))
    print("  the same person may press:", await view.interaction_check(Interaction()))
    press = Interaction(); await view.children[1].callback(press)
    print("  Cancel ->", press.log[0][1], "| file:", request_text())

    print("\n== update now ==")
    i = Interaction(); await cog.update.callback(cog, i, "fix/temperature-alert-layout")
    before = len(activity_log.since(0, "bot_update"))
    press = Interaction(uid=42); await i.log[0][2]["view"].children[0].callback(press)
    text = request_text()
    lines = dict(line.split("=", 1) for line in text.strip().splitlines())
    print("  reply ->", press.log[0][1].split("(")[0].strip(), "| request id in the reply:", lines["id"] in press.log[0][1])
    print("  file keys:", sorted(lines), "| branch:", lines["branch"], "| by:", lines["by"], "| mode:", oct(US.REQUEST_FILE.stat().st_mode & 0o777))
    print("  time is now:", abs(int(lines["time"]) - __import__("time").time()) < 5)
    print("  activity log:", len(activity_log.since(0, "bot_update")) - before)
    i = Interaction(); await cog.update.callback(cog, i, "main")
    await i.log[0][2]["view"].children[0].callback(Interaction())
    again = dict(line.split("=", 1) for line in request_text().strip().splitlines())
    print("  a second request has a new id:", again["id"] != lines["id"], "| branch:", again["branch"])

    print("\n== the data folder cannot be written ==")
    saved = US.REQUEST_FILE
    US.REQUEST_FILE = Path("/proc/forbidden/update_request")
    i = Interaction(); await cog.update.callback(cog, i, "main")
    press = Interaction(); await i.log[0][2]["view"].children[0].callback(press)
    print("  ->", press.log[0][1][:46])
    US.REQUEST_FILE = saved

asyncio.run(main())
