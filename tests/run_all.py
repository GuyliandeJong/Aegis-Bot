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
    "check_library_flow.py": [
        "'The Dark Knight Rises'  year=2012 season=None episode=None",
        "'Fallout'                year=None season=2 episode=3",
        "'2001 A Space Odyssey'   year=1968",
        "'Blade Runner 2049'      year=2017",
        "movie appears after a while -> [('Now in Jellyfin', '**The Dark Knight Rises (2012)** is ready to watch on **Home**.', '<@222>')]",
        "episode not there yet       -> []",
        "episode there               -> [('Now in Jellyfin', '**Fallout — Season 2, episode 3**",
        "season pack, S02 present    -> ['**Fallout — Season 2** is ready",
        "Music category              -> []",
        "turned off -> follow-up started: False",
    ],
    "check_monitors.py": [
        "CPUMonitor: sent 7 notifications",
        "MemoryMonitor: sent 7 notifications",
        "rises and stays at 81     : . warning . . . . .",
        "warning then critical     : . warning . critical .",
        "legacy state 'ok', 50, 50 : ('. | .'",
        "High Temperature | Temperature Recovered",
    ],
    "check_jellyfin_extras.py": [
        "season 2: complete=False episodes=['t5', 't6']",
        "season 3: complete=True episodes=['t7', 't8']",
        "keys: ['S02E02', 'S02E03', 'S02E04', 'S03']",
        "series pairs: [('1', 'x'), ('2', 'y')]",
        "1920x800 -> 1080p",
        "**Friend** has a copy that fits your torrent preferences better (score 55 against -50)",
        "1080p · H.264 · 10.00 GiB · score 40",
        "Your copy on **Home** fits your torrent preferences best (score 55 against 40).",
        "09:00 run   -> posts: 0 | requests: 0",
        "**Gravity Falls (2012)** on Friend: Season 1 (1 episode), Season 2",
        "18:00 again -> posts: 1",
        "next day, nothing new -> posts: 1",
        "friend added season 3 -> posts: 2",
        "picker: ['Season 1 — 1 missing episode', 'Season 2', 'Season 3']",
        "[Jellyfin download complete] ['Copied 1 file(s) from **Friend**.'] | mention: <@7>",
        "Movie 1: first shown as 'Jellyfin download queued'",
        "Movie 2: positions shown ['Position 3 in the queue.', 'Position 2 in the queue.', "
        "'Position 1 in the queue.'] -> Jellyfin download complete",
        "Series/Gravity Falls (2012)/Season 1/fgf.S01E03.mkv",
    ],
    "check_jellyfin_lookup.py": [
        "'Dune'       -> ['Home', 'Jellyfin 2']",
        "'Dune 1984'  -> []",
        "'Alien'      -> []",
        "[Already available on Jellyfin 2]",
    ],
    "check_server_health.py": [
        "PS1 Status         Power supply OK   95 W out, 750 W rated",
        "/server line: 2 power supplies OK · 2 fans OK · drawing 182 W",
        "single unmonitored PSU: 2 fans OK",
        "voltage sensors read: ['System Board 1.8V SW PG', 'System Board 2.5V SW PG']",
        "/server line, real server: 2 fans OK · 2 voltage sensors OK",
        "critical  Voltage Failure — System Board 1.8V SW PG | OK → Critical",
        "first check, alerts: 0",
        "critical  Power supply Failure — PS2 Status | OK → Critical",
        "warning   Fan Warning — System Board Fan1",
        "recovered Power supply Recovered — PS2 Status | Critical → OK",
        "warning   Hardware Missing — System Board Fan2",
        "iDRAC lists nothing -> new alerts: 0",
        "Sunday 09:00 -> summaries: 0",
        "again the same day -> summaries: 1",
        "Torrents completed: 1 torrent, 8.00 GiB / • Arrival (2016) · 8.00 GiB",
        "busy week field: 40 torrents, 820.00 GiB / The full list is in the thread below.",
        "thread: Weekly summary 2026-10-25 | messages: 2 | lines: 40",
        "first: • Some Long Movie Title Number 00 (2000) · 1.00 GiB",
        "Alerts: 1 critical, 1 warning, 1 recovered.",
        "Internet: 2 outages, 12m 5s in total, longest 10m.",
        "SSD 64% left       -> healthy",
        "SSD 9% left        -> warning",
        "failure predicted  -> critical",
        "alert line: **SSD Life Left** 9%",
        "next Sunday disks: `/` 52.00 GiB of 100.00 GiB (52%), +12.00 GiB this week",
        "disconnected -> written: False",
        "running, fresh heartbeat       exit=0 sent=[]",
        "running, heartbeat 15 min old  exit=0 sent=['Aegis-Bot is down: no heartbeat for 15 minutes",
        "still stale (no repeat)        exit=0 sent=[]",
        "container stopped              exit=0 sent=['Aegis-Bot is down: the aegis-bot container is not running.",
        "container back                 exit=0 sent=['Aegis-Bot is running again.",
        "state kept in home: up | written to data/: False",
    ],
    "check_torrent_controls.py": [
        "setting off, non-admin adds -> qBittorrent adds: 1 | Torrent Added",
        "setting on, admin adds      -> qBittorrent adds: 1 | Torrent Added",
        "setting on, non-admin adds  -> qBittorrent adds: 0 | Waiting for approval",
        "non-admin presses Approve : Only an administrator can approve requests. | adds: 0",
        "admin approves            -> adds: 1 | Torrent Added | requester recorded: <@4> | Requested by user4 · approved by user3 | controls owner: 4",
        "admin rejects             -> Request rejected | <@6> | adds: 1",
        "requester cancels         -> Request cancelled",
        "non-admin switches : Only a server administrator can change the speed.",
        "admin -> slow      : 🐢 Slow speed is on",
        "admin -> slow again: toggles: 1",
        "admin -> normal    : 🚀 Normal speed is on | toggles: 2",
    ],
    "check_torrent_extras.py": [
        "add again     : (False, '**dune part two** is already on your watchlist.')",
        "over the limit: False",
        "buttons: ['Watch for it']",
        "watchlist           : [('Obscure Film 1971', 'Movies', '55')]",
        "first check: searched ['Obscure Film 1971', 'Second Film'] | posts: 0",
        "1 hour later: searched []",
        "posted in the search channel: <@1> | Found: Obscure Film 1971",
        "left on the watchlist: ['Second Film']",
        "entry without a channel -> alert channel posts: 1 <@3>",
        "stranger may press Show results: False",
        "owner presses Show results -> search: [('Obscure Film 1971', 'Movies')]",
        "not an admin: Only a server administrator can clean up torrents.",
        "order  : ['High ratio', 'Long seeded', 'Low ratio new']",
        "back -> same picker: True | deleted so far: []",
        "confirmed: [('b', True), ('c', True)]",
        "'Dark Knight'              -> True",
        "'dark knight rises 2012'   -> True",
        "'dark kight'               -> False",
        "first page: Page 1 of 3 | options: 25 | previous disabled: True",
        "last page: Page 3 of 3 | options: 10 | next disabled: True",
        "back on page 1, still ticked: ['Torrent 00', 'Torrent 01']",
        "removed across pages: ['h00', 'h01', 'h30']",
        "still needs 30.00 GiB, but only 10.00 GiB",
        "enough space -> messages: 0",
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
        "second download while running -> Jellyfin download queued",
    ],
    "check_series_flow.py": [
        "result   : Jellyfin download complete",
    ],
    "check_backup.py": [
        "run 1: exit=0 ok=True uploaded=True error=''",
        "contains .env: True | jellyfin config: True | sonarr db: True",
        "skipped cache/MediaCover/logs: True",
        "plex: kept databases: True | skipped Cache/Metadata/Media/Logs: True",
        "archive mode: 0o600 | local dir mode: 0o700",
        "docker: ['docker stop jellyfin', 'docker stop sonarr', 'docker start sonarr', 'docker start jellyfin']",
        "project packed once: True",
        "project packed once inside the parent: True",
        "split into parts: True | names: .tar.gz.000 .tar.gz.001 | each at most 2 KiB: True",
        "remote: True | size in status: True",
        "parts checked by checksum: True",
        "local kept: 2 | remote kept: ['2026-01-03', '2026-01-04', '2026-",
        "oldest single files removed: True",
        "archive kept locally after a failed upload: True | size recorded: True",
        "first error shown: True",
        "second run while one is busy: 1 Backup skipped: another backup is still running. | status untouched: True",
        "drafts replaced: true",
        "container will not stop: exit=1 ok=False uploaded=False error='could not stop broken'",
        "no paths set: exit=1 ok=False uploaded=False error='BACKUP_PATHS is not set in .env'",
        "no upload configured: exit=0 ok=True uploaded=False error=''",
        "KiB, uploaded to proton:Backups.",
        "worlds left out of the archive: True | other apps still in: True",
        "local mirror: ['server.properties', 'world/region/r.0.0.mca']",
        "uploaded: ['server.properties', 'world/region/r.0.0.mca'] | crash report skipped: True",
        "order: ['docker stop', 'rclone sync local', 'rclone sync local', 'docker start', 'rclone sync', 'rclone sync']",
        "mirrored: 2 | changed: 3",
        "Mirrored 2 folder(s), 3 file(s) changed.",
        "changed: 0 | previous versions: False",
        "changed: 2 | current: region v2 | deleted from current: True",
        "kept in previous: ['server.properties=motd=a', 'world/region/r.0.0.mca=region v1']",
        "previous runs kept: 2 | oldest removed: True",
        "minecraft-gone does not exist",
        "minecraft-a failed: connection reset",
        "archive still uploaded: True",
        "mirror without upload: exit=0 ok=True uploaded=False error=''",
        "with mirrors : uploaded to proton:Backups. Mirrored 7 folders, 1 file changed.",
        "failed       : upload to proton:Backups failed",
        "never run    : No backup has run yet.",
    ],
    "check_discord_http.py": [
        "callback task: FINISHED",
        '"edit_original", ["Download from Jellyfin"]',
    ],
    "check_seasons_subtitles.py": [
        "[Friend has more seasons]",
        "picker offers: ['Season 2']",
        "[Friend has more episodes]",
        "picker offers: ['Season 2 — 2 missing episodes']",
        "Series/Gravity Falls (2012)/S2/GF.S02E03.mkv",
        "English subtitles: in 2 of 3 files",
        "Amelie: Subtitles: no English (has Dutch, French)",
        "Movies/Parasite (2019)/Parasite.2019.en.srt",
        "[Already available on 2 Jellyfin servers] "
        "view=TorrentJellyfinSearchView",
    ],
    "check_esxi.py": [
        "datastore media      capacity=4000 GiB free=1000 GiB accessible=True",
        "vm homeassistant  poweredOn",
        "calls: ['RetrieveServiceContent', 'Login', 'CreateContainerView', 'RetrievePropertiesEx', 'DestroyView', 'CreateContainerView', 'RetrievePropertiesEx', 'DestroyView', 'Logout']",
        "password sent escaped: True",
        "first check, alerts: []",
        "already over the level at the first check -> [('warning', 'Datastore Filling Up — datastore1'), ('warning', 'Datastore Filling Up — media')]",
        "next check, unchanged -> 2 alert(s) in total",
        "warning   Datastore Filling Up — media",
        "warning   VM Stopped — homeassistant",
        "critical  Datastore Critical — datastore1",
        "recovered VM Running — homeassistant",
        "recovered Datastore Recovered — media",
        "alerts: [('warning', 'ESXi Not Reachable'), ('recovered', 'ESXi Reachable Again')]",
        "not configured -> requests: []",
    ],
    "check_expiry.py": [
        "expired; buttons disabled",
        "'disabled': [True]",
    ],
    "check_restart.py": [
        "after view 5 timed out: ['1']",
        "owner after view stopped: transfer",
        "edited at startup: (1, 'The bot restarted",
        "edited at startup: (3, 'download note', None)",
        "left after startup: []",
        "qBittorrent list requests for both monitors: 1",
        "TorrentMonitor state after two were removed: ['aaa']",
        "5368709120 -> 5.00 GiB | completion alert 5.00 GiB",
        "failures: [('iDRAC', 'could not connect'), "
        "('Friend', 'the API key was refused')]",
    ],
    "check_private_replies.py": [
        "/wan      all private",
        "/server   all private",
        "/idrac    all private",
        "/network  all private",
        "/esxi     all private",
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
