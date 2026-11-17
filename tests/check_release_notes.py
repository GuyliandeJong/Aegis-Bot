"""Release notes per version: the script prints a version's own notes (when its file exists) followed by the shared install notes, replaces the placeholders, refuses a tag that is not a version, and every released tag with a notes file in the repository reads correctly."""
import subprocess
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT / "scripts" / "release_notes.sh"


def run(cwd, *args):
    result = subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr.strip()


def fixture():
    folder = Path(tempfile.mkdtemp(prefix="release-notes-"))
    (folder / ".github" / "releases").mkdir(parents=True)
    (folder / ".github" / "release-notes.md").write_text("INSTALL {{VERSION}} {{TAG}} {{OWNER}} {{REPO}}\n")
    (folder / ".github" / "releases" / "v2.0.0.md").write_text("## What's new in {{VERSION}}\n- a change, see {{REPO}}@{{TAG}}\n")
    return folder


print("== a version with its own notes ==")
folder = fixture()
code, out, _ = run(folder, "v2.0.0", "Some-Owner", "Some-Owner/Repo")
print("  exit:", code)
print("  lines:", out.splitlines())
print("  own notes first:", out.index("What's new") < out.index("INSTALL"))

print("\n== a version without notes of its own ==")
code, out, _ = run(folder, "v2.1.0", "Some-Owner", "Some-Owner/Repo")
print("  exit:", code, "| lines:", out.splitlines())

print("\n== a tag that is not a version ==")
for tag in ("../x", "main", "v1.0/../../x", "v 1", ""):
    code, out, err = run(folder, tag, "o", "o/r")
    print(f"  {tag!r:16} -> exit {code} | stdout empty: {out == ''} | {err.splitlines()[0] if err else ''}")
code, out, err = run(folder, "v2.0.0")
print("  missing arguments -> exit:", code, "|", err)

print("\n== the repository's own notes ==")
releases = sorted(p.stem for p in (PROJECT / ".github" / "releases").glob("v*.md"))
print("  every released version has notes:", all(t in releases for t in ("v1.0.0", "v1.1.0", "v1.2.0")))
problems = []
for tag in releases:
    code, out, err = run(PROJECT, tag, "Some-Owner", "Some-Owner/Repo")
    first = out.splitlines()[0] if out else ""
    if code != 0 or not first.startswith("## ") or tag[1:] not in first or "{{" in out or "## Install" not in out:
        problems.append(tag)
print("  every notes file reads correctly (heading first, placeholders replaced, install notes after):", not problems, problems)
