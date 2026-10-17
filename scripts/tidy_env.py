#!/usr/bin/env python3
"""
Sort .env into the layout of .env.example, without showing any value.

    python3 scripts/tidy_env.py            report setting names only
    python3 scripts/tidy_env.py --write    also write .env.tidy

The report lists names, never values, so it is safe to share: settings
that are duplicated, unknown, required but empty, or equal to their
default. --write creates .env.tidy next to .env with every setting in
the order and under the comments of .env.example, keeping each value
exactly as written. Settings you have not set appear commented out
with their default. Settings the template does not know are kept at
the end. It then checks that .env.tidy holds the same settings with
the same values as .env, and only then tells you how to swap them.
.env itself is never changed.

Duplicates: the last line wins, as for the bot (python-dotenv), Docker
and backup.sh; the earlier lines are left out.
"""

import argparse
import os
import re
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent

KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# Settings the bot cannot start or run its core features without.
REQUIRED = {
    "DISCORD_TOKEN", "GUILD_ID", "ALERT_CHANNEL_ID",
    "IDRAC_HOST", "IDRAC_USERNAME", "IDRAC_PASSWORD",
    "GPTWOL_HOST", "GPTWOL_TARGET_IP", "GPTWOL_TARGET_MAC",
    "QBITTORRENT_HOST", "QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD",
}


def read_env(path: Path) -> dict:
    """
    Parse an env file. Returns:
        values     {name: raw value text}, last line wins
        order      names in order of first appearance
        counts     {name: number of lines setting it}
        comments   number of comment lines
        unreadable line numbers that are neither empty, comment nor setting
        crlf       whether the file has Windows line ends
    """
    raw = path.read_bytes().decode("utf-8", errors="replace")
    info = {"values": {}, "order": [], "counts": {}, "comments": 0,
            "unreadable": [], "crlf": "\r\n" in raw}

    for number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#"):
            info["comments"] += 1
            continue

        match = KEY_LINE.match(line)

        if not match:
            info["unreadable"].append(number)
            continue

        name, value = match.group(1), match.group(2)

        if name not in info["values"]:
            info["order"].append(name)

        info["values"][name] = value
        info["counts"][name] = info["counts"].get(name, 0) + 1

    return info


def read_template(path: Path) -> tuple[list, dict]:
    """The template's lines, and {name: default} for its settings."""
    lines = path.read_text(encoding="utf-8").splitlines()
    defaults = {}

    for line in lines:
        match = KEY_LINE.match(line)

        if match and not line.lstrip().startswith("#"):
            defaults[match.group(1)] = match.group(2)

    return lines, defaults


def _plain(value: str) -> str:
    value = value.strip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]

    return value


def build(env: dict, template_lines: list, defaults: dict) -> str:
    """The tidied file."""
    out = []

    for line in template_lines:
        match = KEY_LINE.match(line)

        if not match or line.lstrip().startswith("#"):
            out.append(line)
            continue

        name = match.group(1)

        if name in env["values"]:
            out.append(f"{name}={env['values'][name]}")
        elif name in REQUIRED:
            out.append(f"# {name}=    <- required, not set")
        else:
            out.append(f"# {name}={defaults[name]}")

    unknown = [name for name in env["order"] if name not in defaults]

    if unknown:
        out += [
            "",
            "# ------------------------------------------------------------",
            "# Not in .env.example: check each, then remove what is not used",
            "# ------------------------------------------------------------",
            "",
        ]
        out += [f"{name}={env['values'][name]}" for name in unknown]

    return "\n".join(out).rstrip("\n") + "\n"


def report(env: dict, defaults: dict) -> list[str]:
    """Findings, by setting name only."""
    values = env["values"]
    lines = [f"{len(values)} settings in .env, {len(defaults)} known to the template."]

    def section(title, names):
        if names:
            lines.append(f"\n{title} ({len(names)}):")
            lines.extend(f"  {name}" for name in names)

    section(
        "Set more than once; the last line is used, the others are left out",
        [f"{n} ×{c}" for n, c in env["counts"].items() if c > 1],
    )
    section(
        "Not in .env.example (kept at the end; possibly old or misspelt)",
        [n for n in env["order"] if n not in defaults],
    )
    section(
        "Required but missing or empty",
        sorted(n for n in REQUIRED if not _plain(values.get(n, ""))),
    )
    section(
        "Same as the default (could be removed)",
        [n for n in env["order"] if n in defaults and _plain(defaults[n])
         and _plain(values[n]) == _plain(defaults[n])],
    )
    section(
        "Set but empty",
        [n for n in env["order"] if n not in REQUIRED and not _plain(values[n])],
    )

    if env["unreadable"]:
        lines.append(
            "\nLines that are not a setting or comment (line numbers): "
            + ", ".join(map(str, env["unreadable"]))
            + ". They are not copied; check them by hand."
        )

    if env["comments"]:
        lines.append(
            f"\n{env['comments']} comment lines are replaced by the template's comments."
        )

    if env["crlf"]:
        lines.append("Windows line ends are converted to Unix ones.")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="write .env.tidy next to .env")
    parser.add_argument("--env", type=Path, default=PROJECT / ".env")
    parser.add_argument("--template", type=Path, default=PROJECT / ".env.example")
    args = parser.parse_args()

    if not args.env.exists():
        print(f"{args.env} does not exist.", file=sys.stderr)
        return 1

    env = read_env(args.env)
    template_lines, defaults = read_template(args.template)

    print("\n".join(report(env, defaults)))

    if not args.write:
        print("\nNothing written. Run with --write to create .env.tidy.")
        return 0

    target = args.env.with_name(".env.tidy")
    previous_umask = os.umask(0o077)

    try:
        target.write_text(build(env, template_lines, defaults), encoding="utf-8")
    finally:
        os.umask(previous_umask)

    target.chmod(0o600)

    # The tidy file must mean exactly the same as the original.
    check = read_env(target)
    changed = sorted(
        name for name in set(env["values"]) | set(check["values"])
        if env["values"].get(name) != check["values"].get(name)
    )

    if changed:
        print(
            f"\nWrote {target}, but it differs for: {', '.join(changed)}. "
            "Do not use it; report these names."
        )
        return 1

    print(
        f"\nWrote {target}: same settings and values as {args.env.name}, "
        "only reordered and commented."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
