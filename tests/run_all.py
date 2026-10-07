"""
Run every scenario check and report which passed.

    python tests/run_all.py            run everything
    python tests/run_all.py expiry     run checks whose name contains "expiry"
    python tests/run_all.py -v         also print each check's output

A check passes when it exits cleanly, prints no traceback, and its
output contains every line listed for it in EXPECTED below.
"""

import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

# Output that must appear for a check to count as passed. These are the
# results that matter; the rest of the output is context for a reader.
EXPECTED = {
    "check_monitors.py": [
        "CPUMonitor: sent 7 notifications",
        "MemoryMonitor: sent 7 notifications",
        "rises and stays at 81     : . warning . . . . .",
        "warning then critical     : . warning . critical .",
        "legacy state 'ok', 50, 50 : ('. | .'",
        "High Temperature | Temperature Recovered",
    ],
    "check_jellyfin_lookup.py": [
        "'Dune'       -> ['Home', 'Jellyfin 2']",
        "'Dune 1984'  -> []",
        "'Alien'      -> []",
        "[Already available on Jellyfin 2]",
    ],
    "check_wol_permissions.py": [
        "reply='Wake-on-LAN Sent'",
        "reply='Computer Already Awake'",
        "admin=False 'Delete files' confirm    -> ['send_message'] "
        "| qBittorrent delete calls: []",
    ],
    "check_download.py": [
        "download                   : ok downloaded=True",
        "again (already there)      : ok downloaded=False",
        "hostile path -> filename   : 'passwd.mkv'",
        "refused (403)              : ERROR",
        "cut off halfway            : ERROR",
        "leftover .part files       : []",
        "Retry               : Jellyfin download complete",
    ],
    "check_series_flow.py": [
        "result   : Jellyfin download complete",
    ],
    "check_discord_http.py": [
        "callback task: FINISHED",
        '"edit_original", ["Download from Jellyfin"]',
    ],
    "check_seasons_subtitles.py": [
        "[Friend has more seasons]",
        "picker offers: ['Season 2']",
        "English subtitles: in 2 of 3 files",
        "Amelie: Subtitles: no English (has Dutch, French)",
        "Movies/Parasite (2019)/Parasite.2019.en.srt",
        "[Already available on 2 Jellyfin servers] "
        "view=TorrentJellyfinSearchView",
    ],
    "check_expiry.py": [
        "expired; buttons disabled",
        "'disabled': [True]",
    ],
    "check_requesters_links.py": [
        "searched Jackett for ['Gattaca 1997']",
        "'Target category: **Series**'",
        "Google results page fetched: False",
        "added by partner (222)          -> mentions <@222>",
        "mentions ['<@222>', '<@333>', '<@111>']",
    ],
}

FAILURE_MARKERS = (
    "Traceback (most recent call last)",
    "EXCEPTION:",
)


def run(name: str) -> tuple[bool, str, list[str]]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(TESTS)]
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    try:
        result = subprocess.run(
            [sys.executable, str(TESTS / name)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "", ["timed out after 300 seconds"]

    output = result.stdout + result.stderr
    problems = []

    if result.returncode != 0:
        problems.append(f"exit code {result.returncode}")

    for marker in FAILURE_MARKERS:
        if marker in output:
            problems.append(f"output contains {marker!r}")

    for line in EXPECTED[name]:
        if line not in output:
            problems.append(f"missing: {line}")

    return not problems, output, problems


def main() -> int:
    arguments = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv[1:]

    names = [
        name
        for name in sorted(EXPECTED)
        if not arguments or any(a in name for a in arguments)
    ]

    failed = 0

    for name in names:
        passed, output, problems = run(name)

        print(f"{'PASS' if passed else 'FAIL'}  {name}")

        for problem in problems:
            print(f"        {problem}")

        if verbose or not passed:
            for line in output.rstrip().splitlines():
                print(f"        | {line}")

        failed += not passed

    print()
    print(f"{len(names) - failed} of {len(names)} checks passed.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
