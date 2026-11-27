"""The host side of /bot update with stubbed git, docker and curl: settings only from the root-owned file (never the project's .env, never executed), the container's mounts checked before every update (writable project, ancestors, the Docker socket, privileged), branch allowlist, cooldown, request files that are symlinks, FIFOs or too large, branch names that are attacks, ignored and failed requests, a plain webhook text, and the installer copying a root-owned script and refusing to switch on with unsafe mounts."""
import fcntl
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
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
HOOK_URL = "https://discord.example/api/webhooks/1/token"

STUBS = {
    "git": r'''#!/bin/bash
echo "git $*" >> "$CALLS"
while [ "$1" = "-c" ]; do shift 2; done
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
  inspect)
    if [[ "$*" == *HostConfig* ]]; then
      [ "${INSPECT_FAILS:-}" = yes ] && exit 1
      echo "${PRIV:-false} ${PIDMODE:-}"
      printf '%b' "${MOUNTS:-}"
    else
      [ "${STOPPED:-}" = yes ] && echo exited || echo running
    fi
    exit 0 ;;
esac
exit 0
''',
    "curl": r'''#!/bin/bash
url=""
while [ $# -gt 0 ]; do
  case "$1" in -d) shift; echo "BODY $1" >> "$HOOKS" ;; https://*) url="$1" ;; esac
  shift
done
echo "URL $url" >> "$HOOKS"
exit 0
''',
}
for name, text in STUBS.items():
    (BIN / name).write_text(text)
    (BIN / name).chmod(0o755)

def clean(text):
    """Output without this run's temporary folders."""
    return re.sub(r"project-\d+", "project", str(text).replace(str(WORK), "<work>"))


USER = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
SAFE = "bind false {p}\\nbind true {p}/data\\n"
COUNTER = [0]


class P:
    """One project folder with its settings file and state folder."""
    def __init__(self, branches="main,feature/new-thing,fix/recheck-notifications,release-1.2,v1.2.0,a_b,A.b", cooldown="0", webhook=HOOK_URL, compose_dir="", conf_extra=""):
        COUNTER[0] += 1
        self.path = WORK / f"project-{COUNTER[0]}"
        (self.path / "scripts").mkdir(parents=True)
        (self.path / "data").mkdir()
        self.state = WORK / f"state-{COUNTER[0]}"
        self.conf = WORK / f"update-{COUNTER[0]}.conf"
        self.conf.write_text(
            f"project={self.path}\nowner={USER}\ncompose_dir={compose_dir}\nwebhook={webhook}\nbranches={branches}\ncooldown={cooldown}\ncontainer=aegis-bot\n{conf_extra}")
        # What the container could plant in the project: none of it is read.
        (self.path / ".env").write_text("WATCHDOG_WEBHOOK_URL=https://evil.example/hook\nUPDATE_COMPOSE_DIR=/tmp/evil\n")
        self.mounts = SAFE.format(p=self.path)
        US.REQUEST_FILE = self.path / "data" / "update_request"

    def request(self, id="1700000000-abcd1234", branch="main", stamp=None, by="42", raw=None):
        text = raw if raw is not None else f"id={id}\nbranch={branch}\ntime={int(time.time()) if stamp is None else stamp}\nby={by}\n"
        (self.path / "data" / "update_request").write_text(text)

    def run(self, args=(), **extra):
        for f in (CALLS, HOOKS):
            f.write_text("")
        env = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "HOME": str(WORK / "home"), "CALLS": str(CALLS), "HOOKS": str(HOOKS),
               "WORK": str(WORK), "UPDATE_READY_TIMEOUT": "2", "XDG_STATE_HOME": str(self.state), "AEGIS_UPDATE_CONF": str(self.conf),
               "AEGIS_UPDATE_TESTING": "1", "MOUNTS": self.mounts, **extra}
        (WORK / "pulled").unlink(missing_ok=True)
        result = subprocess.run(["bash", str(ROOT / "scripts" / "update_host.sh"), *args], capture_output=True, text=True, env=env, timeout=30)
        calls = CALLS.read_text().splitlines()
        hooks = HOOKS.read_text().splitlines()
        return result, calls, hooks


def messages(hooks):
    # The elapsed seconds depend on the clock, so they are always shown as 0.
    return [re.sub(r"ready after \d+s", "ready after 0s", h[len('BODY {"content": "'):].rstrip('"}')) for h in hooks if h.startswith("BODY")]
def urls(hooks): return sorted({h[4:] for h in hooks if h.startswith("URL")})
def git_calls(calls): return [c for c in calls if c.startswith("git ") and "fetch" in c or c.startswith("git -c core.hooksPath=/dev/null -c core.fsmonitor=false")]
def started(calls): return any(c.startswith("git") for c in calls) or any(c.startswith("docker compose") for c in calls)


def main():
    print("== no request ==")
    p = P()
    result, calls, hooks = p.run()
    print("  exit:", result.returncode, "| calls:", calls, "| messages:", hooks)

    print("\n== a valid request ==")
    p = P()
    p.request()
    result, calls, hooks = p.run()
    print("  exit:", result.returncode)
    for call in calls:
        print("   ", call.replace("-c core.hooksPath=/dev/null -c core.fsmonitor=false ", ""))
    for t in messages(hooks):
        print("  message:", t)
    print("  posted only to the settings file's webhook:", urls(hooks) == [HOOK_URL])
    print("  git runs with hooks and fsmonitor off:", all("core.hooksPath=/dev/null -c core.fsmonitor=false" in c for c in calls if c.startswith("git ")))
    result, calls, hooks = p.run()
    print("  the same request again -> calls:", calls, "| messages:", hooks)

    print("\n== a branch only on origin, and DockSTARTer's compose folder ==")
    p = P(compose_dir="/home/user/.docker/compose")
    p.request(id="1700000001-aaaa0001", branch="feature/new-thing")
    result, calls, hooks = p.run(LOCAL_BRANCH="no")
    print("  git:", [c.split("fsmonitor=false ")[1] for c in calls if " switch" in c])
    print("  compose:", [c for c in calls if c.startswith("docker compose")])

    print("\n== already up to date ==")
    p = P(); p.request(id="1700000002-aaaa0002")
    result, calls, hooks = p.run(BEFORE="abc1234", AFTER="abc1234")
    print("  message:", messages(hooks)[-1])

    print("\n== settings come only from the settings file ==")
    p = P(compose_dir="", webhook="")
    p.request(id="1700000003-aaaa0003")
    result, calls, hooks = p.run()
    print("  the project's .env names another webhook and compose folder -> posted:", hooks, "| compose in the project folder:", [c for c in calls if c.startswith("docker compose")][:1])
    for label, extra in [("a command in a value", "webhook=https://x.example/$(touch pwn-a)\n"), ("a line that is a command", "touch pwn-b\n")]:
        p = P(conf_extra=extra if label == "a line that is a command" else "")
        if label == "a command in a value":
            p = P(webhook="https://x.example/$(touch pwn-a)")
        p.request(id="1700000004-conf0000")
        result, calls, hooks = p.run()
        created = any(Path(n).exists() or (WORK / n).exists() or (p.path / n).exists() for n in ("pwn-a", "pwn-b"))
        print(f"  {label:26} -> exit {result.returncode} | nothing created: {not created} | update started: {started(calls)}")
    for label, kwargs in [("a project outside /", {"project": "relative/path"}), ("a project with ..", {"project": "/srv/../etc"}), ("a bad owner", {"owner": "Root User"}), ("a bad cooldown", {"cooldown": "x"}),
                          ("a bad branch list", {"branches": "main;rm"}), ("a bad container", {"container": "a b"}), ("a bad compose folder", {"compose_dir": "/a b"})]:
        p = P(); p.request(id="1700000005-badconf0")
        lines = [line for line in p.conf.read_text().splitlines() if line.split("=")[0] not in kwargs] + [f"{k}={v}" for k, v in kwargs.items()]
        p.conf.write_text("\n".join(lines) + "\n")
        result, calls, hooks = p.run()
        print(f"  {label:26} -> exit {result.returncode} | {clean(result.stderr.strip().split(chr(10))[0])[:30]} | update started: {started(calls)}")
    p = P(); p.request(id="1700000006-noconf000"); p.conf.unlink()
    result, calls, hooks = p.run()
    print("  no settings file           -> exit", result.returncode, "|", clean(result.stderr.split("(")[0].strip()), "| update started:", started(calls))

    print("\n== what the container can reach ==")
    cases = [
        ("project read-only, data writable", lambda p: SAFE.format(p=p), True),
        ("unrelated writable mounts", lambda p: SAFE.format(p=p) + "bind true /mnt/media\\nvolume true /var/lib/docker/volumes/x/_data\\n", True),
        ("project writable", lambda p: f"bind true {p}\\n", False),
        ("project writable, data too", lambda p: f"bind true {p}\\nbind true {p}/data\\n", False),
        ("a folder above the project writable", lambda p: f"bind false {p}\\nbind true {p}/data\\nbind true {Path(p).parent}\\n", False),
        ("/ writable", lambda p: f"bind false {p}\\nbind true /\\n", False),
        ("a writable folder inside the project", lambda p: f"bind false {p}\\nbind true {p}/data\\nbind true {p}/config\\n", False),
        ("a writable folder inside data", lambda p: f"bind false {p}\\nbind true {p}/data/logs\\n", True),
        ("the host mounted read-only at /host", lambda p: SAFE.format(p=p) + "bind false /\\n", False),
        ("/run mounted read-only", lambda p: SAFE.format(p=p) + "bind false /run\\n", False),
        ("the Docker socket mounted", lambda p: SAFE.format(p=p) + "bind false /run/docker.sock\\n", False),
        ("a mount of /mnt read-only", lambda p: SAFE.format(p=p) + "bind false /mnt\\n", True),
    ]
    for label, build, safe in cases:
        p = P(); p.mounts = build(p.path)
        p.request(id=f"17000001{COUNTER[0]:02d}-mounts{COUNTER[0]:03d}")
        result, calls, hooks = p.run()
        checked = p.run(args=["--check-mounts"])[0]
        refused = [t for t in messages(hooks) if "refused" in t]
        print(f"  {label:42} -> update {'runs' if started(calls) else 'refused'} | --check-mounts exit {checked.returncode}" + (f" | {clean(refused[0].split('. See')[0].split('enough: ')[1])[:70]}" if refused else ""))
    p = P(); p.request(id="1700000200-privileged"); result, calls, hooks = p.run(PRIV="true")
    print("  privileged container                       -> update", "runs" if started(calls) else "refused", "|", messages(hooks)[-1].split("enough: ")[1].split(". See")[0])
    p = P(); p.request(id="1700000201-pidhost000"); result, calls, hooks = p.run(PIDMODE="host")
    print("  shares the host's process namespace        -> update", "runs" if started(calls) else "refused", "|", messages(hooks)[-1].split("enough: ")[1].split(". See")[0])
    p = P(); p.request(id="1700000202-noinspect0"); result, calls, hooks = p.run(INSPECT_FAILS="yes")
    print("  docker cannot inspect the container        -> update", "runs" if started(calls) else "refused")

    print("\n== the branch allowlist ==")
    for allowed, branch in [("main", "main"), ("main", "feature/new-thing"), ("main,feature/new-thing", "feature/new-thing"), ("main", "mai"), ("feature/new-thing", "feature/new"), ("main", "main,x")]:
        p = P(branches=allowed); p.request(id=f"1700000300-allow{COUNTER[0]:04d}", branch=branch)
        result, calls, hooks = p.run()
        print(f"  allowed {allowed!r:26} asked {branch!r:22} -> {'runs' if started(calls) else 'refused'}")
    p = P(branches="main"); p.request(id="1700000399-whyrefuse", branch="feature/new-thing")
    result, calls, hooks = p.run()
    print("  the refusal says why:", messages(hooks)[-1])

    print("\n== the cooldown ==")
    p = P(cooldown="300")
    p.request(id="1700000400-first0000"); result, calls, hooks = p.run()
    print("  first update runs:", started(calls))
    p.request(id="1700000401-second000"); result, calls, hooks = p.run()
    print("  a second one right after ->", "runs" if started(calls) else "refused", "|", messages(hooks)[-1])
    p2 = P(cooldown="0"); p2.request(id="1700000402-third0000"); p2.run()
    p2.request(id="1700000403-fourth000"); result, calls, hooks = p2.run()
    print("  cooldown 0 -> a second one runs:", started(calls))

    print("\n== request files that are not plain files ==")
    for label in ("a symlink to another file", "a symlink to a missing file", "a FIFO", "a directory", "a file over 1 KiB", "an empty file"):
        p = P()
        target = p.path / "data" / "update_request"
        secret = WORK / "secret.txt"
        secret.write_text(f"id=1700000500-link0000\nbranch=main\ntime={int(time.time())}\nby=1\n")
        if label.startswith("a symlink to another"): target.symlink_to(secret)
        elif label.startswith("a symlink to a missing"): target.symlink_to(WORK / "nowhere")
        elif label == "a FIFO": os.mkfifo(target)
        elif label == "a directory": target.mkdir()
        elif label.startswith("a file over"): target.write_text(f"id=1700000500-big00000\nbranch=main\ntime={int(time.time())}\nby=1\n" + "x" * 2000)
        else: target.write_text("")
        try:
            result, calls, hooks = p.run()
            outcome = f"exit {result.returncode} | update started: {started(calls)} | messages: {hooks}"
        except subprocess.TimeoutExpired:
            outcome = "HUNG"
        print(f"  {label:28} -> {outcome}")
    p = P(); (p.path / "data").rmdir(); (WORK / "elsewhere").mkdir(exist_ok=True)
    (WORK / "elsewhere" / "update_request").write_text(f"id=1700000501-moved0000\nbranch=main\ntime={int(time.time())}\nby=1\n")
    (p.path / "data").symlink_to(WORK / "elsewhere")
    result, calls, hooks = p.run()
    print("  data/ itself a symlink       -> update started:", started(calls))

    print("\n== branch names that must be refused ==")
    bad = ["--upload-pack=x", "-x", "../x", "x..y", "a//b", "feat/", "x.lock", "$(touch pwn)", "`id`", "a;rm -rf x", "a b", "main\\n", "x|y", "a&b", "x."]
    results = []
    for index, name in enumerate(bad):
        p = P(branches=",".join(["main", name]) if "," not in name else "main")
        p.request(id=f"1700000600-bad{index:05d}", branch=name)
        result, calls, hooks = p.run()
        results.append(not started(calls) and not (p.path / "pwn").exists() and not Path("pwn").exists())
    print("  no git or docker call, nothing created, even when listed as allowed:", all(results), f"({len(bad)} names)")
    plain = []
    for index, name in enumerate(bad):
        p = P(); p.request(id=f"1700000601-bad{index:05d}", branch=name)
        result, calls, hooks = p.run()
        plain.append(not started(calls) and len(messages(hooks)) == 1 and "refused" in messages(hooks)[0])
    print("  and each is refused with a message:", all(plain), f"({len(bad)} names)")

    print("\n== requests that are ignored ==")
    old = int(time.time()) - 3600
    future = int(time.time()) + 3600
    for label, kwargs in [("an old request", {"stamp": old}), ("a request from the future", {"stamp": future}), ("a short id", {"id": "abc"}),
                          ("an id with a slash", {"id": "1700000700-a/b/c/d/e"}), ("a time that is not a number", {"raw": "id=1700000700-abcd1234\nbranch=main\ntime=soon\nby=1\n"}),
                          ("garbage", {"raw": "\x00\x01 not a request"})]:
        p = P(); p.request(**kwargs)
        result, calls, hooks = p.run()
        print(f"  {label:30} -> calls: {calls} | messages: {hooks} | exit: {result.returncode}")

    print("\n== failures are reported ==")
    for stage in ("fetch", "ls-remote", "switch", "pull", "down", "up"):
        p = P(); p.request(id=f"1700000800-fail{stage[:4]}00")
        result, calls, hooks = p.run(FAIL=stage)
        message = messages(hooks)[-1]
        later = [c for c in calls if c.startswith("docker compose") and stage in ("fetch", "ls-remote", "switch", "pull")]
        print(f"  {stage:9} -> {message.split(':')[0].replace('(requested by <@42>)', '').strip()} | {message.split(': ', 1)[1].split('. Last')[0][:62]} | docker started anyway: {bool(later)}")
    p = P(); p.request(id="1700000801-notready0")
    result, calls, hooks = p.run(NO_READY="yes")
    print("  not ready ->", messages(hooks)[-1].split(": ", 1)[1].split(". Last")[0])
    p = P(); p.request(id="1700000802-stopped00")
    result, calls, hooks = p.run(NO_READY="yes", STOPPED="yes")
    print("  stopped   ->", messages(hooks)[-1].split(": ", 1)[1].split(". Last")[0])

    print("\n== the webhook text stays plain ==")
    p = P(); p.request(id="1700000900-plain000")
    result, calls, hooks = p.run(FAIL="fetch")
    body = messages(hooks)[-1]
    print("  quotes, backslashes or newlines inside the text:", any(ch in body for ch in ('"', "\\", "\n")), "| length under 2000:", len(body) < 2000)

    print("\n== no webhook set ==")
    p = P(webhook=""); p.request(id="1700000901-nohook000")
    result, calls, hooks = p.run()
    print("  still updates:", any("pull" in c for c in calls), "| messages:", hooks)

    print("\n== one update at a time ==")
    p = P(); p.request(id="1700000902-locked000")
    (p.state / "aegis-bot").mkdir(parents=True, exist_ok=True)
    handle = open(p.state / "aegis-bot" / "update.lock", "w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    result, calls, hooks = p.run()
    handle.close()
    print("  an update is already running -> calls:", calls, "| messages:", hooks)

    print("\n== the file the bot writes ==")
    p = P()
    request_id = US.write_request("fix/recheck-notifications", 123456789)
    mode = oct(US.REQUEST_FILE.stat().st_mode & 0o777)
    result, calls, hooks = p.run()
    print("  mode:", mode, "| id kept:", request_id in (p.state / "aegis-bot" / "update_last_id").read_text())
    print("  git:", [c.split("fsmonitor=false ")[1] for c in calls if " switch" in c or " pull" in c])
    print("  message:", messages(hooks)[0])

    print("\n== the installer ==")
    prefix = WORK / "prefix"
    p = P()
    shutil.copytree(ROOT / "scripts" / "systemd", p.path / "scripts" / "systemd")
    shutil.copy(ROOT / "scripts" / "install_update_service.sh", p.path / "scripts" / "install_update_service.sh")
    shutil.copy(ROOT / "scripts" / "update_host.sh", p.path / "scripts" / "update_host.sh")

    def install(*args, mounts=None, **env):
        e = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "SUDO_USER": "alex", "AEGIS_UPDATE_ROOT": str(prefix), "AEGIS_UPDATE_TESTING": "1",
             "MOUNTS": mounts if mounts is not None else p.mounts, "CALLS": str(CALLS), "HOOKS": str(HOOKS), "WORK": str(WORK), "HOME": str(WORK / "home"),
             "XDG_STATE_HOME": str(WORK / "install-state"), **env}
        return subprocess.run(["bash", str(p.path / "scripts" / "install_update_service.sh"), *args], capture_output=True, text=True, env=e)

    printed = install("--print").stdout
    print("  --print: no placeholder left:", "__" not in printed, "| user:", "User=alex" in printed)
    print("  watches the request file:", f"PathChanged={p.path}/data/update_request" in printed)
    print("  runs the root-owned copy, not the project's script:", "ExecStart=/bin/bash /usr/local/lib/aegis-bot/update_host.sh" in printed and "scripts/update_host.sh" not in printed.split("ExecStart")[1])
    print("  the unit limits the service:", "NoNewPrivileges=yes" in printed and "ProtectSystem=full" in printed)
    result = install("--webhook", HOOK_URL, "--compose-dir", "/home/alex/.docker/compose", "--branches", "main,fix/x", "--cooldown", "120")
    conf = (prefix / "etc/aegis-bot/update.conf").read_text()
    print("  installs:", result.returncode, "|", result.stdout.splitlines()[0])
    print("  settings file:", sorted(line.split("=")[0] for line in conf.splitlines()), "| branches:", [l for l in conf.splitlines() if l.startswith("branches")][0], "| cooldown:", [l for l in conf.splitlines() if l.startswith("cooldown")][0])
    print("  settings file mode:", oct((prefix / "etc/aegis-bot/update.conf").stat().st_mode & 0o777))
    copy = prefix / "usr/local/lib/aegis-bot/update_host.sh"
    print("  script copied unchanged:", copy.read_bytes() == (ROOT / "scripts" / "update_host.sh").read_bytes(), "| mode:", oct(copy.stat().st_mode & 0o777))
    print("  units written:", sorted(f.name for f in (prefix / "etc/systemd/system").iterdir()))
    result = install("--uninstall")
    print("  uninstall removes everything:", not any((prefix / d).exists() and any((prefix / d).iterdir()) for d in ("etc/systemd/system", "etc/aegis-bot", "usr/local/lib/aegis-bot")))
    shutil.rmtree(prefix, ignore_errors=True)
    result = install(mounts=f"bind true {p.path}\\n")
    print("  a writable project -> exit", result.returncode, "|", clean(result.stderr.splitlines()[0])[:70])
    print("  and no unit is written:", not (prefix / "etc/systemd/system").exists() or not any((prefix / "etc/systemd/system").iterdir()))
    result = install(mounts=SAFE.format(p=p.path) + "bind false /\\n")
    print("  the host mounted -> exit", result.returncode, "|", result.stderr.splitlines()[0][:60])
    for label, args in [("a webhook that is not https", ["--webhook", "http://x.example/h"]), ("a branch list with a space", ["--branches", "main, x"]), ("a relative compose folder", ["--compose-dir", "rel"]), ("a cooldown that is text", ["--cooldown", "soon"]), ("an unknown option", ["--bogus"])]:
        result = install(*args)
        print(f"  {label:30} -> exit {result.returncode} | {result.stderr.strip().splitlines()[0][:48]}")
    result = install(SUDO_USER="root")
    print("  run as root -> exit", result.returncode, "|", result.stderr.strip().splitlines()[0][:40])
    spaced = WORK / "my project"
    (spaced / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "scripts" / "systemd", spaced / "scripts" / "systemd")
    shutil.copy(ROOT / "scripts" / "install_update_service.sh", spaced / "scripts" / "install_update_service.sh")
    result = subprocess.run(["bash", str(spaced / "scripts" / "install_update_service.sh"), "--print"], capture_output=True, text=True)
    print("  a path with a space is refused:", result.returncode, "|", result.stderr.split(":")[0])


main()
