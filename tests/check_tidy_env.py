"""scripts/tidy_env.py on a messy sample: the report names settings but never shows a value, the tidy file keeps every value and the last of duplicates, follows .env.example, and .env itself is left alone."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "tidy_env.py"
ROOT = Path(tempfile.mkdtemp(prefix="aegis-env-"))
# A small template of its own, so this check does not change whenever
# .env.example is reorganised.
TEMPLATE = ROOT / ".env.example"
TEMPLATE.write_text("""# Aegis-Bot settings

# ------------------------------------------------------------
# Discord
# ------------------------------------------------------------

# Bot token. (required)
DISCORD_TOKEN=
# Server ID. (required)
GUILD_ID=
# Channel for alerts. (required)
ALERT_CHANNEL_ID=

# ------------------------------------------------------------
# Thresholds
# ------------------------------------------------------------

FILESYSTEM_WARNING=85
CPU_WARNING=75
MEMORY_WARNING=80

# ------------------------------------------------------------
# iDRAC
# ------------------------------------------------------------

# Address of the iDRAC; its module stays off while this is empty.
IDRAC_HOST=

# ------------------------------------------------------------
# qBittorrent
# ------------------------------------------------------------

QBITTORRENT_PASSWORD=
SEED_DAYS_TARGET=14

# ------------------------------------------------------------
# Jellyfin
# ------------------------------------------------------------

JELLYFIN_1_URL=

# ------------------------------------------------------------
# Portainer
# ------------------------------------------------------------

PORTAINER_URL=
""")
ENV = ROOT / ".env"
SECRETS = ["s3cr3t-token-value", "hunter2-password", "9999-old-guild", "http://only-here:8096", "keep me"]
ORIGINAL = (
    "DISCORD_TOKEN=s3cr3t-token-value\r\n# my own note\r\nGUILD_ID=9999-old-guild\r\n"
    "QBITTORRENT_PASSWORD=\"hunter2-password\"\r\nCPU_WARNING=75\r\nJELLYFIN_1_URL=http://only-here:8096\r\n"
    "OLD_SETTING=keep me\r\nGUILD_ID=123\r\nexport PORTAINER_URL=http://portainer:9000\r\nnot a setting\r\n"
)
ENV.write_bytes(ORIGINAL.encode())


def run(*extra):
    if "--settings" not in extra:
        extra = (*extra, "--settings", str(ROOT / "none.py"))
    result = subprocess.run([sys.executable, str(SCRIPT), "--env", str(ENV), "--template", str(TEMPLATE), *extra],
                            capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


print("== report ==")
code, out = run()
print("  exit:", code, "| no value shown:", not any(s in out for s in SECRETS), "| nothing written:", not (ROOT / ".env.tidy").exists())
for line in out.splitlines():
    if line.startswith(("Set more", "Not in", "Same as", "Lines that")) or line.startswith("  "):
        print("  |", line)

print("\n== write ==")
code, out = run("--write")
tidy = (ROOT / ".env.tidy").read_text()
print("  exit:", code, "| no value shown:", not any(s in out for s in SECRETS), "|", out.strip().splitlines()[-1].split(": ", 1)[1])
print("  mode:", oct((ROOT / ".env.tidy").stat().st_mode & 0o777), "| .env untouched:", ENV.read_bytes() == ORIGINAL.encode())
print("  values kept:", all(s in tidy for s in SECRETS if s != "9999-old-guild"), "| last duplicate wins:", "GUILD_ID=123" in tidy and "9999" not in tidy)
print("  quotes kept:", 'QBITTORRENT_PASSWORD="hunter2-password"' in tidy, "| export dropped:", "PORTAINER_URL=http://portainer:9000" in tidy.splitlines())
print("  unset default commented:", "# MEMORY_WARNING=80" in tidy, "| required marked:", "# ALERT_CHANNEL_ID=    <- required, not set" in tidy,
      "| module setting not marked:", "# IDRAC_HOST=    <- required, not set" not in tidy and "# IDRAC_HOST=" in tidy)
print("  unknown at the end:", tidy.rstrip().endswith("OLD_SETTING=keep me"), "| LF only:", "\r" not in tidy)
order = [l.split("=")[0] for l in tidy.splitlines() if l and not l.startswith("#")]
print("  order follows the template:", order)

print("\n== drop defaults ==")
SETTINGS = ROOT / "settings.py"
ENV.write_text("CPU_WARNING=75\nMEMORY_WARNING=80\nFILESYSTEM_WARNING=90\nSEED_DAYS_TARGET=14\n")
SETTINGS.write_text('X = os.getenv("CPU_WARNING", "75")\nY = os.getenv("MEMORY_WARNING", "70")\n')
code, out = run("--write", "--drop-defaults", "--settings", str(SETTINGS))
tidy = (ROOT / ".env.tidy").read_text()
print("  exit:", code, "|", out.strip().splitlines()[-1].split(": ", 1)[1])
print("  commented out:", [n for n in ("CPU_WARNING", "SEED_DAYS_TARGET") if f"# {n}=" in tidy],
      "| kept (settings.py differs):", "MEMORY_WARNING=80" in tidy.splitlines(),
      "| kept (not default):", "FILESYSTEM_WARNING=90" in tidy.splitlines())
print("  reported as kept:", [l.strip() for l in out.splitlines()[out.splitlines().index(next(l for l in out.splitlines() if "has another one" in l)) + 1:][:1]])
