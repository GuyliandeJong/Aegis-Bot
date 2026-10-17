"""scripts/tidy_env.py on a messy sample: the report names settings but never shows a value, the tidy file keeps every value and the last of duplicates, follows .env.example, and .env itself is left alone."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "tidy_env.py"
TEMPLATE = HERE.parent / ".env.example"
ROOT = Path(tempfile.mkdtemp(prefix="aegis-env-"))
ENV = ROOT / ".env"
SECRETS = ["s3cr3t-token-value", "hunter2-password", "9999-old-guild", "http://only-here:8096", "keep me"]
ORIGINAL = (
    "DISCORD_TOKEN=s3cr3t-token-value\r\n# my own note\r\nGUILD_ID=9999-old-guild\r\n"
    "QBITTORRENT_PASSWORD=\"hunter2-password\"\r\nCPU_WARNING=75\r\nJELLYFIN_1_URL=http://only-here:8096\r\n"
    "OLD_SETTING=keep me\r\nGUILD_ID=123\r\nexport PORTAINER_URL=http://portainer:9000\r\nnot a setting\r\n"
)
ENV.write_bytes(ORIGINAL.encode())


def run(*extra):
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
print("  unset default commented:", "# MEMORY_WARNING=80" in tidy, "| required marked:", "# IDRAC_HOST=    <- required, not set" in tidy)
print("  unknown at the end:", tidy.rstrip().endswith("OLD_SETTING=keep me"), "| LF only:", "\r" not in tidy)
order = [l.split("=")[0] for l in tidy.splitlines() if l and not l.startswith("#")]
print("  order follows the template:", order)
