# Checks

These scripts exercise the bot without Discord, qBittorrent, Jellyfin or
iDRAC. They run the real code against stand-ins on the local machine.

```bash
pip install -r requirements.txt
python tests/run_all.py
```

`python tests/run_all.py -v` prints each script's output;
`python tests/run_all.py expiry` runs only the scripts whose name
contains "expiry".

Run them before pushing a branch. They do not replace trying the change
on the real server: they show the code does what it was written to do,
not that Discord or another person's Jellyfin behaves as assumed.

## What each one covers

| Script | Covers | Stand-ins |
|---|---|---|
| `check_monitors.py` | CPU and memory thresholds, alert cooldown, temperature recovery | Fake services and clock |
| `check_jellyfin_lookup.py` | Searching several Jellyfin servers, title and year matching | Fake HTTP |
| `check_wol_permissions.py` | `/wol`, and who may delete files | Fake Discord objects |
| `check_download.py` | Downloading a file: success, refusal, cut-off, no space, retry, a second download queued | Fake HTTP, real temp files |
| `check_series_flow.py` | Season picker to files on disk | Real discord.py and aiohttp, local fake Jellyfin |
| `check_discord_http.py` | The download button through discord.py's request code | Local fake Discord API |
| `check_seasons_subtitles.py` | Missing seasons and episodes, reusing an existing season folder, subtitle reporting, separate subtitle files | Two local fake Jellyfin servers |
| `check_expiry.py` | Messages disabling their buttons after the timeout | Local fake Discord API |
| `check_private_replies.py` | `/wan`, `/server`, `/idrac` and `/network` reply only to the person who ran them | Reads the source; runs nothing |
| `check_jellyfin_extras.py` | Episode comparison, matching series between libraries, quality and the better-copy note, the daily new-content alert and its picker, the download queue | Two local fake Jellyfin servers, fake channel and clock |
| `check_restart.py` | Buttons marked expired after a restart, the shared torrent list, pruning removed torrents, GiB sizes, the startup service check | Fake channel, local fake qBittorrent, Jackett and Jellyfin |
| `check_requesters_links.py` | Who is mentioned per torrent, and Google share links | Local fake Google and Wikidata |

`isolate.py` points the bot's state files at a temporary folder, so a
check never writes to `/app/data`; checks import it after the project
modules.

The first four replace `discord` and `aiohttp` with `stubs.py`, so they
also run without the requirements installed. The others need them.

## How passing is decided

The scripts print what happened; they are written to be read. A script
passes in `run_all.py` when it exits cleanly, prints no traceback, and
its output contains the lines listed for it in `EXPECTED` at the top of
`run_all.py`. When behaviour changes on purpose, update those lines.

The scripts use fixed local ports 18096 to 18100.

Nothing here contacts the internet. A live check of a share link needs
Google and Wikidata, and Wikidata blocks a client for about 100 seconds
after a burst of requests, so do that sparingly and by hand.
