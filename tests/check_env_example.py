"""
.env.example stays complete and tidy: every setting the code or the host scripts read is listed, nothing is listed that nothing reads, no name twice, every module has its own section that names its required settings, and a long section is split with sub-headings. Also run against deliberately bad samples, to show each problem is caught.
"""
import ast
import logging
import re
import sys
import types
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"

# Names the code mentions that are not settings people put in .env.
IGNORE = {
    "AF_LINK": "a psutil constant",
    "ENV_FILE": "points the checks at an empty file; not for deployments",
    "CRITICAL_DURATION": "suffix the threshold monitor adds to CPU_ or MEMORY_",
    "CRITICAL_RECOVERY_THRESHOLD": "suffix the threshold monitor adds to CPU_ or MEMORY_",
    "RECOVERY_DURATION": "suffix the threshold monitor adds to CPU_ or MEMORY_",
    "RECOVERY_THRESHOLD": "suffix the threshold monitor adds to CPU_ or MEMORY_",
    "WARNING_DURATION": "suffix the threshold monitor adds to CPU_ or MEMORY_",
}

# A module section with more settings than this needs sub-headings.
LONG_SECTION = 12

NAME = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


def settings_read():
    """(names, templates): setting names found in code and host scripts."""
    names, templates = set(), set()

    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"tests", ".git", ".claude"} or "__pycache__" in parts:
            continue

        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME.match(node.value):
                names.add(node.value)
            elif isinstance(node, ast.JoinedStr):
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
                # Only names with a fixed part, such as JELLYFIN_{n}_URL.
                if "{}" in text and re.fullmatch(r"[A-Z0-9_{}]+", text) and re.search(r"[A-Z]{3,}", text):
                    templates.add(re.escape(text).replace(r"\{\}", r"\d+"))
            elif isinstance(node, ast.Call):
                function = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if function == "getenv" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    names.add(node.args[0].value)

    for path in (ROOT / "scripts").glob("*.sh"):
        names |= set(re.findall(r"env_value\s+([A-Z][A-Z0-9_]+)", path.read_text(encoding="utf-8")))

    # "MODULES", "TZ" and the like are read with getenv; "S{}E{}" and
    # other f-strings that are not setting names do not contain "_".
    return names, templates


def parse(text):
    """Sections of an example file: title, banner text, names, sub-headings."""
    sections, current, lines = [], None, text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        if re.match(r"^# -{10,}$", line):
            end = index + 1
            while end < len(lines) and not re.match(r"^# -{10,}$", lines[end]):
                end += 1
            banner = [l[2:] for l in lines[index + 1:end]]
            current = {"title": banner[0] if banner else "", "banner": " ".join(banner), "names": [], "sub": 0}
            sections.append(current)
            index = end
        elif current is not None:
            match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
            if match:
                current["names"].append(match.group(1))
            elif re.match(r"^# -- .+ --$", line):
                current["sub"] += 1
        index += 1

    return sections


def problems(text, read, templates, modules):
    """Everything wrong with an example file, as short strings."""
    found = []
    sections = parse(text)
    listed = [n for section in sections for n in section["names"]]

    for name in sorted(set(listed)):
        if listed.count(name) > 1:
            found.append(f"{name} is listed twice")

    for name in sorted(read - set(listed) - set(IGNORE)):
        found.append(f"{name} is read by the code but missing from the example")

    for pattern in sorted(templates):
        if not any(re.fullmatch(pattern, n) for n in listed):
            found.append(f"no setting like {pattern} in the example")

    for name in sorted(set(listed)):
        if name not in read and not any(re.fullmatch(p, name) for p in templates):
            found.append(f"{name} is in the example but nothing reads it")

    by_module = {}
    for section in sections:
        match = re.match(r"Module: (\w+)\.", section["title"])
        if match:
            by_module[match.group(1)] = section

    for module in modules:
        section = by_module.get(module.name)
        if section is None:
            found.append(f"module {module.name} has no section")
            continue
        for name in module.required:
            if name not in section["names"]:
                found.append(f"{name} (needed by {module.name}) is outside its section")
            if name not in section["banner"]:
                found.append(f"the {module.name} heading does not name {name}")

    for section in sections:
        if len(section["names"]) > LONG_SECTION and not section["sub"]:
            found.append(f"section '{section['title'][:30]}' has {len(section['names'])} settings and no sub-headings")

    return found


from core import modules as MD

read, templates = settings_read()
modules = MD.discover()
text = EXAMPLE.read_text(encoding="utf-8")

print("== .env.example ==")
sections = parse(text)
print("  sections:", len(sections), "| settings:", sum(len(s["names"]) for s in sections), "| modules:", len(modules))
for line in problems(text, read, templates, modules) or ["none"]:
    print("  problem:", line)

print("\n== the same checks catch these ==")
sample = text.replace("PC_GAMES_DIR=\n", "", 1)
print("  a setting removed          :", [p for p in problems(sample, read, templates, modules)])
sample = text + "\nSOME_OLD_SETTING=1\n"
print("  a setting nothing reads    :", [p for p in problems(sample, read, templates, modules)])
sample = text.replace("PC_GAMES_DIR=\n", "PC_GAMES_DIR=\nPC_GAMES_DIR=\n", 1)
print("  a setting listed twice     :", [p for p in problems(sample, read, templates, modules)])
sample = re.sub(r"# Module: minecraft\.", "# Module: mine.", text, count=1)
print("  a module without a section :", [p for p in problems(sample, read, templates, modules)])
sample = text.replace("# On when: UPTIME_URLS", "# On when: nothing", 1) if "# On when: UPTIME_URLS" in text else text.replace("UPTIME_URLS (public", "nothing (public", 1)
print("  a heading that is wrong    :", [p for p in problems(sample, read, templates, modules)])
sample = re.sub(r"^# -- .+ --\n\n", "", text, flags=re.M)
print("  sub-headings removed       :", "long sections flagged" if any("no sub-headings" in p for p in problems(sample, read, templates, modules)) else "NOT CAUGHT")
print("  ignored names, each with a reason:", all(IGNORE.values()))
