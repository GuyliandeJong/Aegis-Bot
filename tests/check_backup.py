"""scripts/backup.sh with fake docker, rclone and curl: archive contents, excludes, stopped containers, local and remote retention, failures, status file; and the summary's Backup line."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(tempfile.mkdtemp(prefix="aegis-bk-"))
PROJECT, APPS, LOCAL, BIN, REMOTE = ROOT / "Aegis-Bot", ROOT / "Docker", ROOT / "local", ROOT / "bin", ROOT / "remote"
for d in (PROJECT / "scripts", PROJECT / "data" / "logs", APPS / "jellyfin" / "config", APPS / "jellyfin" / "cache",
          APPS / "sonarr" / "MediaCover", APPS / "sonarr" / "logs", BIN, REMOTE):
    d.mkdir(parents=True)
shutil.copy(HERE.parent / "scripts" / "backup.sh", PROJECT / "scripts")
(PROJECT / ".env").write_text("DISCORD_TOKEN=secret\n")
(PROJECT / "data" / "state.json").write_text("{}")
(PROJECT / "data" / "logs" / "aegis.log").write_text("x" * 1000)
(APPS / "jellyfin" / "config" / "system.xml").write_text("<x/>")
(APPS / "jellyfin" / "cache" / "big.bin").write_bytes(b"0" * 100_000)
(APPS / "sonarr" / "sonarr.db").write_text("db")
(APPS / "sonarr" / "MediaCover" / "poster.jpg").write_bytes(b"0" * 50_000)
(APPS / "sonarr" / "logs" / "sonarr.txt").write_text("log")
plex = APPS / "plex" / "Library" / "Application Support" / "Plex Media Server"
for sub in ("Cache", "Metadata", "Media/localhost", "Logs", "Plug-in Support/Databases"):
    (plex / sub).mkdir(parents=True)
    (plex / sub / "file.bin").write_text("x")

CALLS = ROOT / "calls.txt"
RUNNING = ROOT / "running"
FAKES = {
    # docker: inspect answers from a file listing running containers; stop/start are recorded.
    "docker": f'''echo "docker $*" >> "{CALLS}"
if [ "$1" = inspect ]; then grep -qx "${{@: -1}}" "{RUNNING}" && echo true || echo false; fi
if [ "$1" = stop ] && [ "$2" = broken ]; then exit 1; fi''',
    # rclone: copy/lsf/deletefile against a local folder standing in for Proton Drive.
    "rclone": f'''echo "rclone $*" >> "{CALLS}"
case "$1" in
  copy) [ -f "{ROOT}/remote_down" ] && {{ echo "couldn't connect" >&2; exit 1; }}; cp "$2" "{REMOTE}/" ;;
  lsf) ls -1 "{REMOTE}" ;;
  deletefile) rm -f "{REMOTE}/${{2##*/}}" ;;
esac''',
    "curl": f'''for a in "$@"; do last="$a"; done; printf '%s\\n' "$7" >> "{ROOT}/webhook.txt"''',
}
for name, body in FAKES.items():
    path = BIN / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)

ENV = dict(os.environ, PATH=f"{BIN}:{os.environ['PATH']}")


def settings(**values):
    lines = ["DISCORD_TOKEN=secret", "WATCHDOG_WEBHOOK_URL=https://discord.example/api/webhooks/1/x",
             f"BACKUP_LOCAL_DIR={LOCAL}"] + [f"{k}={v}" for k, v in values.items()]
    (PROJECT / ".env").write_text("\n".join(lines) + "\n")


def run(label):
    CALLS.write_text("")
    hook = ROOT / "webhook.txt"; hook.write_text("")
    result = subprocess.run(["bash", str(PROJECT / "scripts" / "backup.sh")], env=ENV, capture_output=True, text=True)
    status = json.loads((PROJECT / "data" / "backup_status.json").read_text())
    print(f"{label}: exit={result.returncode} ok={status['ok']} uploaded={status['uploaded']} error={status['error']!r}")
    print("  webhook:", hook.read_text().strip()[12:90])
    return status


settings(BACKUP_PATHS=f"{APPS}/jellyfin {APPS}/sonarr/ '{APPS}/plex'", BACKUP_STOP_CONTAINERS="jellyfin sonarr stopped-app",
         BACKUP_REMOTE="proton:Backups", BACKUP_KEEP_LOCAL="2", BACKUP_KEEP_REMOTE="3")
RUNNING.write_text("jellyfin\nsonarr\n")

print("== first backup ==")
status = run("run 1")
archive = sorted(LOCAL.glob("aegis-backup-*.tar.gz"))[-1]
names = sorted(tarfile.open(archive).getnames())
rel = lambda p: str(p).lstrip("/")
print("  contains .env:", rel(PROJECT / ".env") in names, "| jellyfin config:", rel(APPS / "jellyfin/config/system.xml") in names,
      "| sonarr db:", rel(APPS / "sonarr/sonarr.db") in names)
print("  skipped cache/MediaCover/logs:", not any(x in n for n in names for x in ("/cache/", "/MediaCover/", "/logs/")))
print("  plex: kept databases:", any("Plug-in Support/Databases/file.bin" in n for n in names),
      "| skipped Cache/Metadata/Media/Logs:", not any(f"Plex Media Server/{x}/" in n for n in names for x in ("Cache", "Metadata", "Media", "Logs")))
print("  archive mode:", oct(archive.stat().st_mode & 0o777), "| local dir mode:", oct(LOCAL.stat().st_mode & 0o777))
docker = [l for l in CALLS.read_text().splitlines() if l.startswith("docker stop") or l.startswith("docker start")]
print("  docker:", docker)
print("  remote:", sorted(p.name for p in REMOTE.iterdir()) == [archive.name], "| size in status:", status["size"] == archive.stat().st_size)

print("\n== retention ==")
for n in range(4):
    for f in REMOTE.iterdir():  # make names differ per run, as one minute apart
        pass
    time.sleep(0.01)
    old = REMOTE / f"aegis-backup-2026-01-0{n + 1}_0400.tar.gz"; old.write_text("old")
    oldlocal = LOCAL / f"aegis-backup-2026-01-0{n + 1}_0400.tar.gz"; oldlocal.write_text("old"); os.utime(oldlocal, (1, 1))
run("run 2")
print("  local kept:", len(list(LOCAL.glob("aegis-backup-*.tar.gz"))), "| remote kept:", sorted(p.name[13:23] for p in REMOTE.iterdir()))

print("\n== failures ==")
(ROOT / "remote_down").write_text("")
st = run("upload fails")
print("  archive kept locally after a failed upload:", (LOCAL / st["archive"]).exists(), "| size recorded:", st["size"] > 0)
os.remove(ROOT / "remote_down")
RUNNING.write_text("broken\n")
settings(BACKUP_PATHS=f"{APPS}/jellyfin", BACKUP_STOP_CONTAINERS="broken", BACKUP_REMOTE="")
run("container will not stop")
settings(BACKUP_PATHS=f"{APPS}/missing")
run("missing folder")
settings()
run("no paths set")
settings(BACKUP_PATHS=f"{APPS}/jellyfin")
run("no upload configured")

print("\n== weekly summary line ==")
m = types.ModuleType("utils.logger"); m.logger = types.SimpleNamespace(warning=print, error=print, info=print); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(); sys.modules["config.settings"] = c
sys.path.insert(0, str(HERE.parent))
import monitors.weekly_summary_monitor as WS
WS.WeeklySummaryMonitor.BACKUP_STATUS = PROJECT / "data" / "backup_status.json"
now = time.time()
print("  locally only :", WS.WeeklySummaryMonitor._backup(now - 3600).split(": ", 1)[1])
st = json.loads(WS.WeeklySummaryMonitor.BACKUP_STATUS.read_text()); st.update(uploaded=True, remote="proton:Backups")
WS.WeeklySummaryMonitor.BACKUP_STATUS.write_text(json.dumps(st))
print("  uploaded     :", WS.WeeklySummaryMonitor._backup(now - 3600).split(": ", 1)[1])
print("  older backup :", WS.WeeklySummaryMonitor._backup(now + 3600)[:35])
st.update(ok=False, error="upload to proton:Backups failed"); WS.WeeklySummaryMonitor.BACKUP_STATUS.write_text(json.dumps(st))
print("  failed       :", WS.WeeklySummaryMonitor._backup(now - 3600).split(": ", 1)[1])
WS.WeeklySummaryMonitor.BACKUP_STATUS.unlink()
print("  never run    :", WS.WeeklySummaryMonitor._backup(now))
shutil.rmtree(ROOT, ignore_errors=True)
