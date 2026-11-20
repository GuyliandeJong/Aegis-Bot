"""The host side of /bot update with stubbed git, docker and curl: nothing without a request, the commands for a valid one, each way a request is refused or ignored (branch names that are attacks, bad id, old, repeated), every failure reported, a webhook text that stays plain, one update at a time, the request file the bot writes being read correctly, and the systemd unit installer."""
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aegis").setLevel(logging.CRITICAL)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c

from modules.containers import update_service as US
import isolate  # state files go to a temporary folder

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.mkdtemp(prefix="update-host-", dir=str(isolate.DATA)))
BIN = WORK / "bin"
BIN.mkdir()
CALLS = WORK / "calls.log"
HOOKS = WORK / "hooks.log"

STUBS = {
    "git": r'''#!/bin/bash
echo "git $*" >> "$CALLS"
case "$1" in
  rev-parse) if [ -f "$WORK/pulled" ]; then echo "${AFTER:-def5678}"; else echo "${BEFORE:-abc1234}"; fi ;;
  fetch) [ "${FAIL:-}" = fetch ] && { echo "fatal: unable to access 'https://example.test/r.git'"; exit 1; }; exit 0 ;;
  ls-remote) [ "${FAIL:-}" = ls-remote ] && exit 2; exit 0 ;;
  show-ref) [ "${LOCAL_BRANCH:-yes}" = yes ] && exit 0; exit 1 ;;
  switch) [ "${FAIL:-}" = switch ] && { echo "error: Your local changes would be overwritten"; exit 1; }; exit 0 ;;
  pull) [ "${FAIL:-}" = pull ] && { echo "fatal: Not possible to fast-forward"; exit 1; }; touch "$WORK/pulled"; exit 0 ;;
esac
exit 0
''',
    "docker": r'''#!/bin/bash
echo "docker $*" >> "$CALLS"
case "$1" in
  compose) case "$*" in *" up "*) [ "${FAIL:-}" = up ] && exit 1;; *" down "*|*" rm "*) [ "${FAIL:-}" = down ] && exit 1;; esac; exit 0 ;;
  logs) [ "${NO_READY:-}" = yes ] && echo "starting" || echo "Monitoring framework started"; exit 0 ;;
  inspect) [ "${STOPPED:-}" = yes ] && echo exited || echo running; exit 0 ;;
esac
exit 0
''',
    "curl": r'''#!/bin/bash
while [ $# -gt 0 ]; do [ "$1" = "-d" ] && { shift; echo "$1" >> "$HOOKS"; }; shift; done
exit 0
''',
}
for name, text in STUBS.items():
    (BIN / name).write_text(text)
    (BIN / name).chmod(0o755)


def project(webhook=True, compose_dir=""):
    folder = WORK / f"project-{len(list(WORK.glob('project-*')))}"
    (folder / "scripts").mkdir(parents=True)
    (folder / "data").mkdir()
    shutil.copy(ROOT / "scripts" / "update_host.sh", folder / "scripts" / "update_host.sh")
    shutil.copytree(ROOT / "scripts" / "systemd", folder / "scripts" / "systemd")
    shutil.copy(ROOT / "scripts" / "install_update_service.sh", folder / "scripts" / "install_update_service.sh")
    env = ""
    if webhook:
        env += "WATCHDOG_WEBHOOK_URL=https://discord.example/api/webhooks/1/token\n"
    if compose_dir:
        env += f"UPDATE_COMPOSE_DIR={compose_dir}\n"
    (folder / ".env").write_text(env)
    US.REQUEST_FILE = folder / "data" / "update_request"
    return folder


def request(folder, id="1700000000-abcd1234", branch="main", stamp=None, by="42", raw=None):
    text = raw if raw is not None else f"id={id}\nbranch={branch}\ntime={int(time.time()) if stamp is None else stamp}\nby={by}\n"
    (folder / "data" / "update_request").write_text(text)


def run(folder, **extra):
    for f in (CALLS, HOOKS):
        f.write_text("")
    env = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "HOME": str(WORK / "home"), "CALLS": str(CALLS), "HOOKS": str(HOOKS),
           "WORK": str(WORK), "UPDATE_READY_TIMEOUT": "2", "XDG_STATE_HOME": str(WORK / "state"), **{k: v for k, v in extra.items()}}
    (WORK / "pulled").unlink(missing_ok=True)
    result = subprocess.run(["bash", str(folder / "scripts" / "update_host.sh")], capture_output=True, text=True, env=env, timeout=60)
    calls = [line for line in CALLS.read_text().splitlines()]
    hooks = [line for line in HOOKS.read_text().splitlines()]
    return result, calls, hooks


def texts(hooks): return [h.split('"content": "', 1)[1].rstrip('"}') for h in hooks]


def main():
    print("== no request ==")
    folder = project()
    result, calls, hooks = run(folder)
    print("  exit:", result.returncode, "| calls:", calls, "| messages:", hooks)

    print("\n== a valid request ==")
    folder = project()
    request(folder)
    result, calls, hooks = run(folder)
    print("  exit:", result.returncode)
    for call in calls:
        print("   ", call)
    for t in texts(hooks):
        print("  message:", t)
    result, calls, hooks = run(folder)
    print("  the same request again -> calls:", calls, "| messages:", hooks)

    print("\n== a branch only on origin, and DockSTARTer's compose folder ==")
    folder = project(compose_dir="/home/user/.docker/compose")
    request(folder, id="1700000001-aaaa0001", branch="feature/new-thing")
    result, calls, hooks = run(folder, LOCAL_BRANCH="no")
    print("  git:", [c for c in calls if c.startswith("git switch")])
    print("  compose:", [c for c in calls if c.startswith("docker compose")])

    print("\n== already up to date ==")
    folder = project()
    request(folder, id="1700000002-aaaa0002")
    result, calls, hooks = run(folder, BEFORE="abc1234", AFTER="abc1234")
    print("  message:", texts(hooks)[-1])

    print("\n== branch names that must be refused ==")
    bad = ["--upload-pack=x", "-x", "../x", "x..y", "a//b", "feat/", "x.lock", "$(touch pwn)", "`id`", "a;rm -rf x", "a b", "main\\n", "x|y", "a&b", "x."]
    results = []
    for index, name in enumerate(bad):
        folder = project()
        request(folder, id=f"1700000100-bad{index:05d}", branch=name)
        result, calls, hooks = run(folder)
        refused = not calls and len(hooks) == 1 and "refused" in hooks[0]
        results.append(refused and not (folder / "pwn").exists() and not Path("pwn").exists())
    print("  refused, no git or docker call, nothing created:", all(results), f"({len(bad)} names)")
    ok = ["main", "feature/ops-additions", "fix/recheck-notifications", "release-1.2", "v1.2.0", "a_b", "A.b"]
    accepted = []
    for index, name in enumerate(ok):
        folder = project()
        request(folder, id=f"1700000200-ok{index:06d}", branch=name)
        result, calls, hooks = run(folder)
        accepted.append(any(c.startswith("git fetch") for c in calls))
    print("  ordinary names accepted:", all(accepted), f"({len(ok)} names)")

    print("\n== requests that are ignored ==")
    old = int(time.time()) - 3600
    future = int(time.time()) + 3600
    for label, kwargs in [("an old request", {"stamp": old}), ("a request from the future", {"stamp": future}), ("a short id", {"id": "abc"}),
                          ("an id with a slash", {"id": "1700000300-a/b/c/d/e"}), ("a time that is not a number", {"raw": "id=1700000300-abcd1234\nbranch=main\ntime=soon\nby=1\n"}),
                          ("an empty file", {"raw": ""}), ("garbage", {"raw": "\x00\x01 not a request"})]:
        folder = project()
        request(folder, **kwargs)
        result, calls, hooks = run(folder)
        print(f"  {label:30} -> calls: {calls} | messages: {hooks} | exit: {result.returncode}")

    print("\n== failures are reported ==")
    for stage in ("fetch", "ls-remote", "switch", "pull", "down", "up"):
        folder = project()
        request(folder, id=f"1700000400-fail{stage[:4]}00")
        result, calls, hooks = run(folder, FAIL=stage)
        message = texts(hooks)[-1]
        later = [c for c in calls if c.startswith("docker compose") and stage in ("fetch", "ls-remote", "switch", "pull")]
        print(f"  {stage:9} -> {message.split(':')[0].replace('(requested by <@42>)', '').strip()} | {message.split(': ', 1)[1].split('. Last')[0][:62]} | docker started anyway: {bool(later)}")
    folder = project(); request(folder, id="1700000401-notready0")
    result, calls, hooks = run(folder, NO_READY="yes")
    print("  not ready ->", texts(hooks)[-1].split(": ", 1)[1].split(". Last")[0])
    folder = project(); request(folder, id="1700000402-stopped00")
    result, calls, hooks = run(folder, NO_READY="yes", STOPPED="yes")
    print("  stopped   ->", texts(hooks)[-1].split(": ", 1)[1].split(". Last")[0])

    print("\n== the webhook text stays plain ==")
    folder = project(); request(folder, id="1700000500-plain000")
    result, calls, hooks = run(folder, FAIL="fetch")
    payload = hooks[-1]
    body = payload.split('"content": "', 1)[1].rstrip('"}')
    print("  quotes, backslashes or newlines inside the text:", any(ch in body for ch in ('"', "\\", "\n")), "| length under 2000:", len(payload) < 2000)

    print("\n== no webhook set ==")
    folder = project(webhook=False); request(folder, id="1700000501-nohook000")
    result, calls, hooks = run(folder)
    print("  still updates:", any(c.startswith("git pull") for c in calls), "| messages:", hooks)

    print("\n== one update at a time ==")
    folder = project(); request(folder, id="1700000502-locked000")
    state = WORK / "state" / "aegis-bot"
    state.mkdir(parents=True, exist_ok=True)
    handle = open(state / "update.lock", "w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    result, calls, hooks = run(folder)
    handle.close()
    print("  an update is already running -> calls:", calls, "| messages:", hooks)

    print("\n== the file the bot writes ==")
    folder = project()
    request_id = US.write_request("fix/recheck-notifications", 123456789)
    mode = oct(US.REQUEST_FILE.stat().st_mode & 0o777)
    result, calls, hooks = run(folder)
    print("  mode:", mode, "| id kept:", request_id in (WORK / "state" / "aegis-bot" / "update_last_id").read_text())
    print("  git:", [c for c in calls if c.startswith("git switch") or c.startswith("git pull")])
    print("  message:", texts(hooks)[0])

    print("\n== the units ==")
    folder = project()
    installer = [("bash", str(folder / "scripts" / "install_update_service.sh"), "--print")]
    printed = subprocess.run(installer[0], capture_output=True, text=True, env={**os.environ, "SUDO_USER": "alex"}).stdout
    print("  no placeholder left:", "__" not in printed, "| user:", "User=alex" in printed)
    print("  watches the request file:", f"PathChanged={folder}/data/update_request" in printed)
    print("  runs the script:", f"ExecStart=/bin/bash {folder}/scripts/update_host.sh" in printed)
    print("  path unit starts the service:", "Unit=aegis-bot-update.service" in printed)
    spaced = WORK / "my project"
    (spaced / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "scripts" / "systemd", spaced / "scripts" / "systemd")
    shutil.copy(ROOT / "scripts" / "install_update_service.sh", spaced / "scripts" / "install_update_service.sh")
    result = subprocess.run(["bash", str(spaced / "scripts" / "install_update_service.sh"), "--print"], capture_output=True, text=True)
    print("  a path with a space is refused:", result.returncode, "|", result.stderr.split(":")[0])


main()
