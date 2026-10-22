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
| `check_library_flow.py` | Reading title, year, season and episode from release names; the Jellyfin refresh and "Now in Jellyfin" message after a torrent completes | Local fake Jellyfin |
| `check_monitors.py` | CPU and memory thresholds, alert cooldown, temperature recovery | Fake services and clock |
| `check_jellyfin_lookup.py` | Searching several Jellyfin servers, title and year matching | Fake HTTP |
| `check_server_health.py` | Power supplies, fans and voltages, the `/server` line, the weekly summary with outages, SSD wear, the heartbeat, `scripts/watchdog.sh` | Fake iDRAC data, clock, qBittorrent; fake `docker` and `curl` for the script |
| `check_torrent_controls.py` | Admin approval off and on (approve, reject, cancel, who may press what), `/torrent speed` | Fake qBittorrent and Discord objects |
| `check_torrent_extras.py` | `/torrent info` name matching, the watchlist (button, store, twice-daily check, notification), `/torrent cleanup` with paging, the free-space warning | Fake qBittorrent, Jackett results and channels |
| `check_wol_permissions.py` | `/wol`, and who may delete files | Fake Discord objects |
| `check_download.py` | Downloading a file: success, refusal, cut-off, no space, retry, a second download queued | Fake HTTP, real temp files |
| `check_series_flow.py` | Season picker to files on disk | Real discord.py and aiohttp, local fake Jellyfin |
| `check_backup.py` | `scripts/backup.sh`: archive contents and excludes, stopping and restarting containers, local and remote retention, failures, the status file; the summary's Backup line | Fake `docker`, `rclone` and `curl`, real `tar` on temporary folders |
| `check_discord_http.py` | The download button through discord.py's request code | Local fake Discord API |
| `check_seasons_subtitles.py` | Missing seasons and episodes, reusing an existing season folder, subtitle reporting, separate subtitle files | Two local fake Jellyfin servers |
| `check_summary_new_content.py` | The weekly summary's "New in Jellyfin" field: grouping movies and episodes per server, paging by DateCreated, a server that does not answer, the overflow thread, turning it off | Two local fake Jellyfin servers |
| `check_esxi.py` | ESXi over the vSphere SOAP API: login, datastores, VMs, logout; datastore and VM alerts; host not reachable | Local fake ESXi `/sdk` |
| `check_maintenance.py` | Maintenance mode: muted levels, critical alerts passing, activity log, restart, expiry by the monitor and by the next alert, the end message, ending early | Fake channel |
| `check_uptime.py` | Public service checks: 4xx is up, 5xx/timeout/refused/expired certificate is down, alert after two failures, back up with duration, certificate warning and critical once, renewal, restarts, feature off | Local HTTP and HTTPS servers with test certificates in `tests/certs/` (test CA, keys committed on purpose), moved clock |
| `check_fluxer.py` | Alerts mirrored to a Fluxer webhook: off when not set, the embed per level, readable timestamps and mentions, limits, slow or failing Fluxer not delaying Discord, limited logging | Local fake Fluxer webhook |
| `check_expiry.py` | Messages disabling their buttons after the timeout | Local fake Discord API |
| `check_help.py` | `/help`: which commands an administrator, a member and a `/container` user see, the grouping, one command in detail, autocomplete, embed limits on a large tree | Fake command tree of real discord.py objects |
| `check_private_replies.py` | `/wan`, `/server`, `/idrac` and `/network` reply only to the person who ran them | Reads the source; runs nothing |
| `check_jellyfin_extras.py` | Episode comparison, matching series between libraries, quality and the better-copy note, the daily new-content alert and its picker, the download queue | Two local fake Jellyfin servers, fake channel and clock |
| `check_restart.py` | Buttons marked expired after a restart, the shared torrent list, pruning removed torrents, GiB sizes, the startup service check | Fake channel, local fake qBittorrent, Jackett and Jellyfin |
| `check_requesters_links.py` | Who is mentioned per torrent, and Google share links | Local fake Google and Wikidata |
| `check_vpn.py` | The VPN check: leak alert with masked addresses, the disconnected warning and recoveries, qBittorrent before 5.1, the `/torrent status` VPN field | Local fake qBittorrent, stubbed WAN IP |

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
