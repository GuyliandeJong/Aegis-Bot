"""scripts/backup.sh with fake docker, rclone and curl: archive contents, excludes, stopped containers, local and remote retention, failures, status file, mirrored folders that only upload changes; and the summary's Backup line."""
import json
import os
import shutil
import stat
import subprocess
import sys
import io
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
    # rclone: a local folder stands in for Proton Drive (proton:Backups).
    "rclone": f'''echo "rclone $*" >> "{CALLS}"
exec python3 "{BIN}/fake_rclone.py" "$@"''',
    "curl": f'''for a in "$@"; do last="$a"; done; printf '%s\\n' "$7" >> "{ROOT}/webhook.txt"''',
}
(BIN / "fake_rclone.py").write_text(f'''
import fnmatch, os, shutil, sys
from pathlib import Path
REMOTE, ROOT = Path({str(REMOTE)!r}), Path({str(ROOT)!r})
where = lambda p: Path(p.replace("proton:Backups", str(REMOTE), 1))
args = [a for a in sys.argv[1:] if a.startswith("--") is False]
opts = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
excludes = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--exclude=")]
if "--backup-dir" in sys.argv: opts["backup-dir"] = sys.argv[sys.argv.index("--backup-dir") + 1]; args.remove(opts["backup-dir"])
cmd = args[0]
def skipped(rel):
    for x in excludes:
        if x.endswith("/**") and x[:-3] in rel.split("/")[:-1]: return True
        if "/" not in x and fnmatch.fnmatch(rel.rsplit("/", 1)[-1], x): return True
    return False
if cmd == "copy":
    if (ROOT / "remote_down").exists():
        # The cause first, then the retries that trip over the draft it left.
        sys.exit("2026/10/11 04:00:01 ERROR : big.tar.gz: Failed to copy: couldn't connect\\n"
                 "2026/10/11 04:00:09 ERROR : big.tar.gz: Failed to copy: a draft exist")
    (ROOT / "draft_env").write_text(os.environ.get("RCLONE_PROTONDRIVE_REPLACE_EXISTING_DRAFT", ""))
    shutil.copytree(args[1], where(args[2]), dirs_exist_ok=True)
elif cmd == "lsf":
    d = where(args[1])
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if "--files-only" not in sys.argv and p.is_dir(): print(p.name + "/")
            if "--dirs-only" not in sys.argv and p.is_file(): print(p.name)
elif cmd == "deletefile": where(args[1]).unlink(missing_ok=True)
elif cmd == "purge": shutil.rmtree(where(args[1]))
elif cmd == "sync":
    if (ROOT / "mirror_down").exists() and "proton:" in args[2]: sys.exit("connection reset")
    src, dst = where(args[1]), where(args[2]); dst.mkdir(parents=True, exist_ok=True)
    old = where(opts["backup-dir"]) if "backup-dir" in opts else None
    want = {{str(p.relative_to(src)) for p in src.rglob("*") if p.is_file() and not skipped(str(p.relative_to(src)))}}
    have = {{str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file()}}
    def keep(rel):
        if old: (old / rel).parent.mkdir(parents=True, exist_ok=True); shutil.move(dst / rel, old / rel)
        else: (dst / rel).unlink()
    for rel in sorted(want):
        if rel in have and (dst / rel).read_bytes() == (src / rel).read_bytes(): continue
        if rel in have: keep(rel)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src / rel, dst / rel)
        print(f"INFO  : {{rel}}: Copied ({{'replaced existing' if rel in have else 'new'}})", file=sys.stderr)
    for rel in sorted(have - want):
        keep(rel); print(f"INFO  : {{rel}}: Deleted", file=sys.stderr)
''')
for name, body in FAKES.items():
    path = BIN / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)

ENV = dict(os.environ, PATH=f"{BIN}:{os.environ['PATH']}")


def settings(**values):
    lines = ["DISCORD_TOKEN=secret", "WATCHDOG_WEBHOOK_URL=https://discord.example/api/webhooks/1/x",
             f"BACKUP_LOCAL_DIR={LOCAL}"] + [f"{k}={v}" for k, v in values.items()]
    (PROJECT / ".env").write_text("\n".join(lines) + "\n")


def members(folder):
    """Names in a split archive: the parts joined in order, as a restore does."""
    parts = sorted(Path(folder).glob("*.tar.gz.*"))
    return tarfile.open(fileobj=io.BytesIO(b"".join(p.read_bytes() for p in parts))).getnames()


def newest():
    return max(LOCAL.glob("aegis-backup-*"), key=os.path.getmtime)


def run(label):
    CALLS.write_text("")
    hook = ROOT / "webhook.txt"; hook.write_text("")
    result = subprocess.run(["bash", str(PROJECT / "scripts" / "backup.sh")], env=ENV, capture_output=True, text=True)
    status = json.loads((PROJECT / "data" / "backup_status.json").read_text())
    print(f"{label}: exit={result.returncode} ok={status['ok']} uploaded={status['uploaded']} error={status['error']!r}")
    print("  webhook:", hook.read_text().strip()[12:90])
    return status


settings(BACKUP_PATHS=f"{APPS}/jellyfin {APPS}/sonarr/ '{APPS}/plex'", BACKUP_STOP_CONTAINERS="jellyfin sonarr stopped-app",
         BACKUP_REMOTE="proton:Backups", BACKUP_KEEP_LOCAL="2", BACKUP_KEEP_REMOTE="3", BACKUP_PART_SIZE="2K")
RUNNING.write_text("jellyfin\nsonarr\n")

print("== first backup ==")
status = run("run 1")
archive = newest()
names = sorted(members(archive))
parts = sorted(p.name for p in archive.iterdir())
print("  split into parts:", len(parts) > 1, "| names:", parts[0][-11:], parts[1][-11:], "| each at most 2 KiB:", all(p.stat().st_size <= 2048 for p in archive.iterdir()))
rel = lambda p: str(p).lstrip("/")
print("  contains .env:", rel(PROJECT / ".env") in names, "| jellyfin config:", rel(APPS / "jellyfin/config/system.xml") in names,
      "| sonarr db:", rel(APPS / "sonarr/sonarr.db") in names)
print("  skipped cache/MediaCover/logs:", not any(x in n for n in names for x in ("/cache/", "/MediaCover/", "/logs/")))
print("  plex: kept databases:", any("Plug-in Support/Databases/file.bin" in n for n in names),
      "| skipped Cache/Metadata/Media/Logs:", not any(f"Plex Media Server/{x}/" in n for n in names for x in ("Cache", "Metadata", "Media", "Logs")))
print("  archive mode:", oct(next(archive.iterdir()).stat().st_mode & 0o777), "| local dir mode:", oct(LOCAL.stat().st_mode & 0o777))
docker = [l for l in CALLS.read_text().splitlines() if l.startswith("docker stop") or l.startswith("docker start")]
print("  docker:", docker)
print("  project packed once:", sum(1 for n in names if n == rel(PROJECT / ".env")) == 1)
print("  remote:", sorted(p.name for p in (REMOTE / archive.name).iterdir()) == parts, "| size in status:", status["size"] >= sum(p.stat().st_size for p in archive.iterdir()))
print("  parts checked by checksum:", "--checksum" in [l for l in CALLS.read_text().splitlines() if l.startswith("rclone copy")][0].split())

print("\n== retention ==")
for n in range(4):
    for f in REMOTE.iterdir():  # make names differ per run, as one minute apart
        pass
    time.sleep(0.01)
    old = REMOTE / f"aegis-backup-2026-01-0{n + 1}_0400.tar.gz"; old.write_text("old")
    oldlocal = LOCAL / f"aegis-backup-2026-01-0{n + 1}_0400.tar.gz"; oldlocal.write_text("old"); os.utime(oldlocal, (1, 1))
run("run 2")
print("  local kept:", len(list(LOCAL.glob("aegis-backup-*"))), "| remote kept:", sorted(p.name[13:23] for p in REMOTE.iterdir()),
      "| oldest single files removed:", not (REMOTE / "aegis-backup-2026-01-01_0400.tar.gz").exists() and not (REMOTE / "aegis-backup-2026-01-02_0400.tar.gz").exists())

print("\n== failures ==")
(ROOT / "remote_down").write_text("")
st = run("upload fails")
print("  archive kept locally after a failed upload:", (LOCAL / st["archive"]).exists(), "| size recorded:", st["size"] > 0)
print("  first error shown:", st["error"].startswith("upload to proton:Backups failed: ERROR : big.tar.gz: Failed to copy: couldn't connect (full log:"))
print("  drafts replaced:", (ROOT / "draft_env").read_text())
os.remove(ROOT / "remote_down")
before = (PROJECT / "data" / "backup_status.json").read_text()
with open(LOCAL / ".lock", "w") as held:
    import fcntl; fcntl.flock(held, fcntl.LOCK_EX)
    second = subprocess.run(["bash", str(PROJECT / "scripts" / "backup.sh")], env=ENV, capture_output=True, text=True)
print("  second run while one is busy:", second.returncode, second.stderr.strip(), "| status untouched:", (PROJECT / "data" / "backup_status.json").read_text() == before)
RUNNING.write_text("broken\n")
settings(BACKUP_PATHS=f"{APPS}/jellyfin", BACKUP_STOP_CONTAINERS="broken", BACKUP_REMOTE="")
run("container will not stop")
settings(BACKUP_PATHS=f"{APPS}/missing")
run("missing folder")
settings(BACKUP_PATHS=f"{APPS}/jellyfin", BACKUP_EXCLUDE="*/cache/* /path/from/docker-inspect/for/ente-minio")
run("exclude path missing")
settings(BACKUP_PATHS=f"{APPS}", BACKUP_EXCLUDE=f"{APPS}/sonarr */config/*", BACKUP_REMOTE="")
st = run("exclude by full path")
names = members(LOCAL / st["archive"])
print("  sonarr left out:", not any("/sonarr" in n for n in names), "| pattern applied:", not any("/config/" in n for n in names), "| jellyfin kept:", any("/jellyfin" in n for n in names))
settings()
run("no paths set")
settings(BACKUP_PATHS=f"{APPS}/jellyfin")
run("no upload configured")
settings(BACKUP_PATHS=f"{ROOT}")
run("parent folder listed")
names = members(newest())
print("  project packed once inside the parent:", names.count(rel(PROJECT / ".env")) == 1)

print("\n== mirrored folders ==")
for name, files in {"minecraft-a": {"world/region/r.0.0.mca": "region v1", "server.properties": "motd=a", "logs/latest.log": "log"},
                    "minecraft-b": {"world/level.dat": "level", "crash-reports/crash.txt": "boom"}}.items():
    for sub, text in files.items():
        (APPS / name / sub).parent.mkdir(parents=True, exist_ok=True); (APPS / name / sub).write_text(text)
shutil.rmtree(REMOTE); REMOTE.mkdir()
MC_A, MC_B = APPS / "minecraft-a", APPS / "minecraft-b"
settings(BACKUP_PATHS=f"{APPS}", BACKUP_SYNC_PATHS=f"{MC_A} {MC_B}/", BACKUP_STOP_CONTAINERS="mc-a",
         BACKUP_REMOTE="proton:Backups", BACKUP_KEEP_REMOTE="2")
RUNNING.write_text("mc-a\n")
st = run("mirror run 1")
names = members(LOCAL / st["archive"])
print("  worlds left out of the archive:", not any("minecraft-" in n for n in names), "| other apps still in:", rel(APPS / "sonarr/sonarr.db") in names)
mirror, current = LOCAL / "mirror" / rel(MC_A), REMOTE / "mirror" / "current" / rel(MC_A)
print("  local mirror:", sorted(str(p.relative_to(mirror)) for p in mirror.rglob("*") if p.is_file()))
print("  uploaded:", sorted(str(p.relative_to(current)) for p in current.rglob("*") if p.is_file()),
      "| crash report skipped:", not (REMOTE / "mirror" / "current" / rel(MC_B) / "crash-reports").exists())
calls = [l.split()[:2] + (["local"] if "/mirror/current/" not in l and l.startswith("rclone sync") else []) for l in CALLS.read_text().splitlines()]
order = [" ".join(c) for c in calls if c[0] == "docker" and c[1] in ("stop", "start") or c[:2] == ["rclone", "sync"]]
print("  order:", order)
print("  mirrored:", st["mirrored"], "| changed:", st["changed"])
print("  webhook:", (ROOT / "webhook.txt").read_text().split("uploaded to proton:Backups.")[1].strip()[:45])
st = run("mirror run 2, nothing changed")
print("  changed:", st["changed"], "| previous versions:", (REMOTE / "mirror" / "previous").exists())
(MC_A / "world/region/r.0.0.mca").write_text("region v2"); (MC_A / "server.properties").unlink()
st = run("mirror run 3, one changed, one deleted")
previous = next((REMOTE / "mirror" / "previous").iterdir()) / rel(MC_A)
print("  changed:", st["changed"], "| current:", (current / "world/region/r.0.0.mca").read_text(), "| deleted from current:", not (current / "server.properties").exists())
print("  kept in previous:", sorted(f"{p.relative_to(previous)}={p.read_text()}" for p in previous.rglob("*") if p.is_file()))
for day in ("2026-01-01_0400", "2026-01-02_0400"):
    (REMOTE / "mirror" / "previous" / day).mkdir()
run("mirror run 4")
print("  previous runs kept:", len(list((REMOTE / "mirror" / "previous").iterdir())), "| oldest removed:", not (REMOTE / "mirror" / "previous" / "2026-01-01_0400").exists())
shutil.rmtree(REMOTE / "mirror" / "current")
st = run("mirror run 5, local copy up to date but nothing on Proton")
print("  counted as uploaded:", st["changed"])
settings(BACKUP_PATHS=f"{APPS}/jellyfin", BACKUP_SYNC_PATHS=f"{APPS}/minecraft-gone")
run("mirror folder missing")
settings(BACKUP_PATHS=f"{APPS}/jellyfin", BACKUP_SYNC_PATHS=f"{MC_A}", BACKUP_REMOTE="proton:Backups")
(ROOT / "mirror_down").write_text("")
st = run("mirror upload fails")
print("  archive still uploaded:", (REMOTE / st["archive"]).exists())
os.remove(ROOT / "mirror_down")
settings(BACKUP_PATHS=f"{APPS}/jellyfin", BACKUP_SYNC_PATHS=f"{MC_A}")
run("mirror without upload")

print("\n== weekly summary line ==")
m = types.ModuleType("utils.logger"); m.logger = types.SimpleNamespace(warning=print, error=print, info=print); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(); sys.modules["config.settings"] = c
sys.path.insert(0, str(HERE.parent))
import modules.general.weekly_summary_monitor as WS
WS.WeeklySummaryMonitor.BACKUP_STATUS = PROJECT / "data" / "backup_status.json"
now = time.time()
print("  locally only :", WS.WeeklySummaryMonitor._backup(now - 3600).split(": ", 1)[1])
st = json.loads(WS.WeeklySummaryMonitor.BACKUP_STATUS.read_text()); st.update(uploaded=True, remote="proton:Backups")
WS.WeeklySummaryMonitor.BACKUP_STATUS.write_text(json.dumps(st))
print("  uploaded     :", WS.WeeklySummaryMonitor._backup(now - 3600).split(": ", 1)[1])
st.update(mirrored=7, changed=1); WS.WeeklySummaryMonitor.BACKUP_STATUS.write_text(json.dumps(st))
print("  with mirrors :", WS.WeeklySummaryMonitor._backup(now - 3600).split(", ", 1)[1])
st.update(mirrored=0)
print("  older backup :", WS.WeeklySummaryMonitor._backup(now + 3600)[:35])
st.update(ok=False, error="upload to proton:Backups failed"); WS.WeeklySummaryMonitor.BACKUP_STATUS.write_text(json.dumps(st))
print("  failed       :", WS.WeeklySummaryMonitor._backup(now - 3600).split(": ", 1)[1])
WS.WeeklySummaryMonitor.BACKUP_STATUS.unlink()
print("  never run    :", WS.WeeklySummaryMonitor._backup(now))
shutil.rmtree(ROOT, ignore_errors=True)
