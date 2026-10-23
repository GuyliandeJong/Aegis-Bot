# Features

What each part of Aegis-Bot does, in detail. Every feature belongs to a
module (see the README); a module whose settings are not filled in is
off, and its commands and alerts do not exist.

## Commands

| Command | What it does | Who can use it | Module |
|---|---|---|---|
| `/torrent search` | Checks Jellyfin first, then searches Jackett and adds the chosen result to qBittorrent | Everyone | torrents |
| `/torrent add` | Adds a magnet link to qBittorrent | Everyone | torrents |
| `/torrent list` | Lists torrents, with filter, sort and live refresh | Everyone | torrents |
| `/torrent info` | Details and controls for one torrent | Everyone | torrents |
| `/torrent status` | qBittorrent version, transfer totals and whether it is behind its VPN | Everyone | torrents |
| `/torrent watchlist` | Titles you are waiting for; remove them | Everyone (own list) | torrents |
| `/torrent cleanup` | Finished torrents by ratio and seeding time; remove them | Administrators | torrents |
| `/torrent speed` | Show qBittorrent's speed limits; switch between normal and slow | Everyone sees; administrators switch | torrents |
| `/cpu`, `/memory`, `/filesystems`, `/network` | Live readings from the host | Everyone | host |
| `/storage`, `/temperature`, `/idrac` | RAID, sensors and iDRAC details through Redfish | Everyone | idrac |
| `/server` | Server model, service tag, BIOS and OS through Redfish | Administrators | idrac |
| `/esxi` | ESXi datastores (usage), virtual machines (running or not) and their snapshots | Administrators | esxi |
| `/maintenance start`, `end`, `status` | Mutes all alerts except critical ones for 30 minutes to 24 hours, ends it early, shows what was muted | Administrators | general |
| `/minecraft` | Minecraft servers: online or not, version, players and latency | Everyone (private reply) | minecraft |
| `/wan` | Public IP address | Administrators | wan |
| `/wol` | Wakes the configured computer, or reports that it is already awake | Administrators | wol |
| `/jellyfin playing` | What is being played on your own Jellyfin server now: who, on which device, progress, and whether it transcodes and why | Administrators (private reply) | jellyfin |
| `/jellyfin movies` | Looks up a movie on all Jellyfin servers, with watched state; the title has autocomplete | Everyone (private reply) | jellyfin |
| `/container` | Lists, inspects and controls Docker containers through Portainer: `list`, `info`, `logs`, `start`, `stop`, `restart`, `kill`, `remove` | Only the users in `PORTAINER_ALLOWED_USER_IDS` | containers |
| `/ping`, `/hello` | Checks that the bot responds | Everyone | general |
| `/help` | Lists the commands you can use, by area; `/help <command>` shows one command's options (autocomplete) | Everyone (private reply) | general |

`/wan`, `/server`, `/idrac`, `/network`, `/esxi` and `/minecraft` reply
privately: only the person who ran the command sees the answer.

"Administrators" is Discord's administrator permission, set as the
command default. It can be changed per user or role in Discord under
Server Settings > Integrations.

`/help` builds its list from the commands the bot has loaded, so a new
command shows up without changes. It leaves out what the person cannot
run: administrator commands for members, `/container` for anyone not in
`PORTAINER_ALLOWED_USER_IDS`, and `/torrent cleanup` for
non-administrators. It goes by the command defaults and does not see
changes made under Server Settings > Integrations.

Inside `/torrent info`, removing a torrent is open to whoever requested
it. Removing a torrent together with its files requires administrator
permission.

## Torrent search and Jellyfin

For the Movies and Series categories, `/torrent search` asks every
configured Jellyfin server before searching for torrents.

```text
/torrent search <title>
        |
        v
Ask all Jellyfin servers
        |
        +-- Primary server has it ......... "Already available on <server>"
        |                                   [Search for torrent]
        |
        +-- Only another server has it .... "Available on <server>"
        |                                   [Download from <server>]
        |                                   [Search for torrent]
        |
        +-- Primary has the series, but
        |   another server has more
        |   seasons or episodes ........... "<server> has more seasons"
        |                                   or "<server> has more episodes"
        |                                   [Download missing from <server>]
        |                                   [Search for torrent]
        |
        +-- Nobody has it ................. Jackett search results
```

Instead of a title, a Google share link (`https://share.google/…`) can
be pasted as the search. The bot reads the title from the link and asks
Wikidata for the year and whether it is a film or a series. For a film
the year is added to the search; if the chosen category does not match
(a series searched under Movies, or the reverse), the right one is used.
Only share links are accepted, and the Google results page itself is
never downloaded.

The primary server is `JELLYFIN_1`. The download button only appears
when a library folder is configured for the category.

### Downloading from another Jellyfin server

- Movies are copied to `JELLYFIN_MOVIES_DIR/<Title (Year)>/`.
- Series ask for a season and copy only that season to
  `JELLYFIN_SERIES_DIR/<Title (Year)>/Season NN/`.
- Original file names are kept.
- A file is written as `<name>.part` and renamed once complete.
- Files that already exist are skipped, never overwritten, so a season
  that stopped halfway can simply be requested again.
- New folders and files get the owner of the library folder.
- Progress is shown in Discord, the requester is mentioned at the end,
  and the primary library is refreshed.
- One download runs at a time. Further requests wait in a queue; their
  message shows their place and updates when it changes.

For a series already in the primary library, the episodes of both
servers are compared by season and episode number. Whole missing
seasons and single missing episodes are offered. They are copied into
the folders the series already uses, including an existing season
folder whatever it is called. A season in which your library has an
episode without a number is never offered partly, because that file
might be one of the "missing" ones.

### Quality

For movies, the availability message shows each server's copy as
resolution, codec, size and score, for example
`1080p · HEVC · 8.20 GiB · score 55`. The score is the Movies rules in
`config/torrent_preferences.json` (resolution plus codec, the same
scores used to rank torrents). A line below says which copy fits your
preferences best, or that they are equal. The bot never replaces
library files; that is left to you.

### New episodes on another server

Once a day the bot compares every series in the primary library with
the other servers and posts in the alert channel which ones have new
seasons or episodes there. Series are matched by their TVDB, TMDB or
IMDb id, or by title and year. Only what was not reported before is
posted. Anyone can pick a series from the list; the bot then posts the
usual download message for that person.

```text
JELLYFIN_NEW_CONTENT_ALERTS=true   # false turns it off
JELLYFIN_NEW_CONTENT_HOUR=10       # hour of the day, container time
```

### Subtitles

The preferred subtitle language is `SUBTITLE_LANGUAGE` (default
English).

- For a movie, the availability message says whether the other server
  has subtitles in that language, and whether they are inside the video
  file or a separate file.
- For a season, the progress and result messages say how many episodes
  have them.
- Subtitles inside the video file come along with it. A separate text
  subtitle file in the preferred language is saved next to the video as
  `<video name>.<language>.srt`. Image-based subtitle files are not
  copied.

Torrent search results say nothing about subtitles: Jackett provides
only the release name, not the files inside.

Missing subtitles for movies in the primary library are fetched through
Jellyfin's subtitle providers, so the Open Subtitles plugin must be
installed and logged in on that server:

- When a downloaded movie shows up ("Now in Jellyfin"), the bot first
  asks Jellyfin to search its providers in `SUBTITLE_LANGUAGE` and
  download the best result, and the message says what happened
  ("Subtitles: English added from Open Subtitles.").
- Once a day at `SUBTITLE_HOUR` (default 6), up to
  `SUBTITLE_DAILY_LIMIT` movies already in the library get the same,
  newest additions first. A movie without results is tried again after
  30 days; a refused download (the provider's daily limit) ends the
  run and is tried again the next day.
- The best result is an exact match for the file first, then the
  `SUBTITLE_HEARING_IMPAIRED` preference, then the most downloads.
  Forced and machine-translated subtitles are never taken.
- The weekly summary shows what was added.

Series episodes get the same with `SUBTITLE_SERIES` (default on): a
single downloaded episode right away, season packs and existing
episodes in the daily run, which they share with movies.
`SUBTITLE_AUTO_DOWNLOAD=false` turns all of it off.

The other server's API key must be allowed to download media.

### Movies on all servers

`/jellyfin movies <title>` looks a movie up on every Jellyfin server:
each match once, with the servers it is on and whether it was watched
there (✅ watched, ⚪ not watched). While you type the title, Discord
suggests matching titles from all servers with these marks; the list
behind the suggestions is at most ten minutes old, the reply itself is
read fresh. Movies are matched across servers by their TMDb or IMDb id,
otherwise by title and year.

Watched state is per Jellyfin user: set `JELLYFIN_<n>_USER` to your
user name on each server. A server without it shows ▫️, and the reply
says which setting is missing or which user was not found. The reply
is private.

### Watchlist

When `/torrent search` finds nothing, the message offers **Watch for
it**. Twice a day the bot looks again: first on every Jellyfin server,
then on Jackett with the same search and relevance filter as
`/torrent search`. When the title turns up, the person who asked is
mentioned in the channel they searched in, with a **Show results**
button that runs the normal search. The title is then removed from the
watchlist. Each person can watch up to 25 titles.

### Seeding and cleanup

`/torrent cleanup` lists finished torrents, those that reached a
seeding target first, with ratio, seeding time and size, 25 per page
with Previous and Next buttons. Torrents chosen on one page stay
chosen on the others; **Choose all ✅** chooses every torrent that
reached a target, on all pages at once (press again to clear). Remove
them with or without their files;
removing files asks for confirmation. Only administrators can use it,
and the reply is visible to them only.

```text
SEED_RATIO_TARGET=2     # a ratio counted as seeded enough
SEED_DAYS_TARGET=14     # or this many days of seeding
```

Once a week the bot posts the torrents that reached a target in the
alert channel: how many, their total size and the list (the first
ones, when they do not all fit). **Remove these torrents (keep files)**
removes them from qBittorrent and leaves the files on disk, so the
library keeps them; **Dismiss** closes the suggestion. Only
administrators can press either, and the message then says what
happened ("Removed 5 torrents, 120.00 GiB still on disk."). The
buttons work for two days. Nothing is posted when no torrent reached
a target.

```text
CLEANUP_REMINDER=true        # false turns it off
CLEANUP_REMINDER_DAY=saturday
CLEANUP_REMINDER_HOUR=11     # container time
```

qBittorrent's own share limits (Options > BitTorrent) can stop or
remove torrents automatically; the bot only shows and removes on
request.

### Speed

`/torrent speed` shows the current download and upload limits. With a
choice, an administrator switches between normal speed and qBittorrent's
alternative ("slow") limits, which are set in qBittorrent under Options
> Speed. qBittorrent can also switch them on a schedule there.

### Approval

Off by default. With `REQUEST_APPROVAL=true`, a torrent that someone
without administrator permission chooses or adds is not added yet: the
message shows "Waiting for approval" with **Approve** and **Reject**
for administrators and **Cancel request** for the person who asked,
and the alert channel gets a link to it. Approved torrents are added
for the person who asked: they are mentioned when it completes and own
its controls. Administrators' own torrents are added at once. A request
nobody answers expires after a day.

### Free space

After a torrent is added, the bot compares what is left to download
with the free space qBittorrent reports, and privately warns the
requester when it does not fit. A magnet's size is only known once
qBittorrent has its metadata, so the check waits up to two minutes for
it. qBittorrent reports the free space of its default save path; a
category saved on another disk is compared against that same number.

## Monitoring

Monitors run in the background and post to the alert channel when a
state changes.

| Monitor | Watches | Interval |
|---|---|---|---|
| CPU, Memory | Sustained high usage, with separate recovery thresholds | 60 s |
| Filesystem | Usage of the host's mounted filesystems | 60 s |
| Temperature | iDRAC temperature sensors | 60 s |
| Storage | RAID controller, virtual disks and physical drives | 60 s |
| Hardware | Power supply and fan health through iDRAC | 60 s |
| Event log | New entries in iDRAC's System Event Log | 5 min |
| WAN | Internet connectivity and public IP changes | 60 s |
| Jellyfin new content | New seasons and episodes on the other servers | Once a day |
| Subtitles | Missing movie subtitles, a few per run | Once a day |
| Containers | Crashed, unhealthy and restarting Docker containers, through Portainer | 60 s |
| Backup | Last successful backup older than `BACKUP_MAX_AGE_DAYS` (8) | Hourly |
| Watchlist | Titles people are waiting for | Twice a day |
| Weekly summary | Disk growth with a "full in" estimate, completed torrents, Jellyfin copies, new in Jellyfin, what was watched, alerts, container updates | Once a week |
| Seed cleanup | Suggests removing torrents that are seeded enough | Once a week |
| Heartbeat | Writes `data/heartbeat` for the watchdog script | 60 s |
| ESXi | Datastore usage, virtual machines stopping, old snapshots, host reachable | 5 min |
| Maintenance | Posts the end of maintenance mode when its time runs out | 60 s |
| Uptime | Public services answering, TLS certificates expiring | 5 min |
| Minecraft | Players joining and leaving (optional), watched servers down | 60 s |
| Torrent | Completed downloads | 10 s |
| Torrent health | Stalled, errored, stopped and resumed torrents | 10 s |
| VPN | qBittorrent using the home address, or reporting no connection | 2 min |

The container monitor alerts once when a container crashes (exits
with another code than a normal stop: 0, 137 or 143), turns unhealthy
or is being restarted by Docker, and once when it is fine again. Stops
by you, by `/container stop` or by the backup give no alert. It needs
the Portainer settings; `CONTAINER_ALERTS_IGNORE` leaves containers
out, and `CONTAINER_ALERTS=false` turns it off.

After an internet outage of at least `WAN_RECOVERY_MIN_OUTAGE` seconds
(also one too short for a "WAN Connection Lost" alert), the containers
in `WAN_RECOVERY_RESTART` are restarted through Portainer, for example
`newt`, whose Pangolin tunnel does not come back by itself. The result
is posted, and the weekly summary counts the restarts under Internet.
These containers must not be in `PORTAINER_PROTECTED`.

Filesystem and temperature alerts for the same component are at least
`ALERT_COOLDOWN` seconds apart, so a value hovering on a threshold does
not alert on every crossing. A component becoming critical is always
reported immediately.

When a Movies or Series torrent completes, the bot asks the primary
Jellyfin server to scan its library, then looks for the title every
90 seconds for up to 15 minutes. When it is there (for a series: the
episode, or for a season pack any episode of that season), the
requester is mentioned with "Now in Jellyfin". Title, year, season and
episode are read from the release name. If nothing turns up, nothing
is posted; that usually means the category saves outside the library.
`JELLYFIN_REFRESH_ON_COMPLETE=false` turns this off.

Torrent notifications (completed, stalled, error) mention the person
who added the torrent through the bot. Torrents added some other way,
for example in qBittorrent itself, mention `DISCORD_USER_ID`.

The VPN monitor checks that qBittorrent (in the `qbittorrentvpn`
container) is not torrenting over the home line. qBittorrent 5.1 and
later report the external address peers see; when that equals the home
address found by the WAN monitor, "Torrents Not Behind VPN" is posted
(critical, once), and "Torrents Behind VPN Again" when it changes.
Addresses in these alerts show only the first two numbers
(`203.0.x.x`). When qBittorrent reports "disconnected" three checks in
a row (six minutes), "qBittorrent Disconnected" is posted; a failed
tunnel is the usual cause. Older qBittorrent versions do not report
their address, so only the connection is watched. `VPN_CHECK=false`
turns this off. `/torrent status` shows the result as VPN: OK, Not
behind VPN or Unknown.

Monitor state is kept in `data/` so a restart does not repeat alerts.

The hardware monitor reports a power supply or fan whose health
changes (Critical, Warning, back to OK), and one that iDRAC stops
listing. `/server` also shows how many power supplies and fans are OK
and how many watts the server draws, under "Hardware Health". On a
server with a single power supply that iDRAC cannot read, iDRAC
reports neither and both are left out. The system board's voltage
("power good") sensors are watched in any case, together with the
fans: a failing power supply or rail shows there. This shows what iDRAC reports:

```bash
docker exec aegis-bot python scripts/redfish_power_check.py
```

The event log monitor reads iDRAC's System Event Log (SEL) every five
minutes. The first check only records what is already there; after that
each new Critical entry is posted as critical and each new Warning
entry as a warning, with iDRAC's message, the sensor type and the time.
New entries from one check are grouped into one alert per severity
(at most ten lines, then "and N more"). Informational (OK) entries are
left out unless `IDRAC_EVENT_LOG_ALL=true`. If iDRAC has no such log
(HTTP 404), that is logged once and the monitor stops until the next
restart. The Lifecycle log is not watched.

```text
IDRAC_EVENT_LOG=true         # false turns it off
IDRAC_EVENT_LOG_ALL=false    # true: also post OK entries as info
```

The weekly summary is posted in the alert channel:

```text
WEEKLY_SUMMARY=true          # false turns it off
WEEKLY_SUMMARY_DAY=sunday
WEEKLY_SUMMARY_HOUR=10       # container time
WEEKLY_SUMMARY_NEW_CONTENT=true   # false leaves out "New in Jellyfin"
WEEKLY_SUMMARY_WATCH_STATS=true   # false leaves out "Watched this week"
WATCHTOWER_CONTAINER=watchtower
```

Disk growth is measured from one summary to the next, so the first
summary only shows current usage. Completed torrents and Jellyfin copies are
listed in full, with readable titles ("Inception (2010)", "Fallout —
S02E03") and their size. When a list does not fit in the summary, the
bot opens a thread under it with the complete list; it needs the
"Create Public Threads" permission in the alert channel for that, and
otherwise posts the list below the summary. "🆕 New in Jellyfin" lists
the movies ("Arrival (2016)") and episodes ("Fallout — Season 2: 3
episodes", or "Fallout — S02E03" for one) added to each configured
Jellyfin server that week, by the date Jellyfin added them, with the
counts per server on its first line ("Home: 4 movies, 2 series ·
Friend: 10 movies"). A server that does not answer is named ("Friend
did not answer"). The summary also counts internet
outages (number, total and longest) confirmed by the WAN monitor.

"🍿 Watched this week" shows what was watched on the primary Jellyfin
server (`JELLYFIN_1_*`): total hours, hours for the five busiest users,
the five most watched titles (episodes counted towards their series)
and the share of plays that were transcoded. Jellyfin keeps no viewing
history itself, so this needs the **Playback Reporting** plugin
(Dashboard → Plugins → Catalog → Playback Reporting, then restart
Jellyfin). It only records plays from the moment it is installed.
Without it the field says "Needs the Playback Reporting plugin on
<server>". Music is left out, and so are users excluded in the
plugin's own settings. The bot reads the plugin's data with its SQL
query endpoint, which needs the API key in `JELLYFIN_1_API_KEY` (API
keys have administrator rights).

The last field, "Container updates", lists the containers Watchtower
updated that week, with their image and date, read from the log of the
container named `WATCHTOWER_CONTAINER` through Portainer. It needs the
Portainer settings of `/container` and shows "Not available." without
them or when that container does not exist. A container Watchtower
restarted only because a linked container was updated is marked as
such. Docker keeps the log only as long as the container exists, so
updates from before Watchtower itself was recreated are not counted.

Physical drives alert when iDRAC predicts a failure, and SSDs also when
their remaining write endurance drops to `SSD_LIFE_WARNING` percent
(default 10). Hard disks do not report this.

### Maintenance mode

`/maintenance start` with a duration (30 minutes, 1, 2, 4, 8 or 24
hours) and an optional reason mutes warnings, recoveries, info and
success messages, for example while disks are swapped or the host is
rebooted. Critical alerts still come through, with "(during
maintenance)" in the title. Starting it posts who started it, until
when and why in the alert channel; starting it again while it is on
moves the end time and keeps what was muted so far.

When it ends, after the chosen time or with `/maintenance end`, the bot
posts one message: "Maintenance ended — N alerts muted:" with the muted
titles grouped and counted, and the number per level.
`/maintenance status` shows the same while it is on (private reply).

The state is kept in `data/maintenance.json`, so a restart does not end
maintenance. Muted alerts are still counted in the weekly summary, and
the weekly summary itself is posted during maintenance too.

### Fluxer

Alerts can also be posted to a [Fluxer](https://fluxer.app) channel.
This is alerts only: the commands, buttons and dropdowns stay on
Discord until Fluxer supports slash commands and interactions. Discord
remains the main channel.

In Fluxer, create a webhook for the channel (channel settings,
Webhooks) and put its URL in `.env`:

```text
FLUXER_WEBHOOK_URL=https://<api>/v1/webhooks/<id>/<token>
```

Every alert, recovery and weekly summary in the Discord alert channel
then also appears in Fluxer as an embed with the same title, text,
color and fields. Discord timestamps are written out in UTC
("2026-10-19 04:00 UTC"), mentions as names, and nobody is pinged
there. Lists the weekly summary puts in a Discord thread, buttons and
the Jellyfin download pickers are not mirrored.

Posting to Fluxer runs in the background with a 10-second limit, so a
slow or broken Fluxer never delays a Discord alert. Failures are
logged as `[Fluxer] Could not mirror alert`, at most once per five
minutes. Empty `FLUXER_WEBHOOK_URL` (the default) turns it off.

### ESXi

When ESXi is configured, the bot checks the host every five minutes
through its vSphere API (`https://<host>/sdk`). It alerts when a
datastore reaches `ESXI_DATASTORE_WARNING` percent used (default 85),
`ESXI_DATASTORE_CRITICAL` (default 95) or becomes inaccessible, when
a running virtual machine stops or is suspended, and when the host
does not answer three checks in a row. A datastore that is already
over a level when the bot first sees it is reported once. `/esxi` shows
the same information. Create a separate ESXi user with the read-only role for
the bot; it needs nothing more.

A virtual machine with a snapshot older than `ESXI_SNAPSHOT_DAYS`
(default 7) gets an "Old Snapshot" warning naming the snapshots and
their age, repeated once a day while it stays, and one "Snapshot
Removed" when it is gone. `0` turns this off. `/esxi` lists every VM
with snapshots ("📸 2 snapshots, oldest 12 days", then each name and
age); ⚠️ marks one over the limit.

```text
ESXI_HOST=
ESXI_USERNAME=
ESXI_PASSWORD=
ESXI_DATASTORE_WARNING=85
ESXI_DATASTORE_CRITICAL=95
# Days before a snapshot counts as old; 0 = no snapshot warnings.
ESXI_SNAPSHOT_DAYS=7
# Virtual machines not to watch, comma separated.
ESXI_IGNORE_VMS=
```

### Public services

`UPTIME_URLS` lists the services that should be reachable from the
internet, for example the ones published through Pangolin, as full
URLs separated by commas. Every `UPTIME_INTERVAL` seconds (default
300) the bot requests each one. A service is down when it does not
answer within `UPTIME_TIMEOUT` seconds (default 15), its certificate
is not valid, or it answers with HTTP 500 or higher; a login page
answering 401 or 403, or a redirect, counts as up. After two failed
checks in a row "Service Down" is posted with the reason, and "Service
Back Up" once it answers again, with how long it was down. For https
URLs the bot also reads the certificate: "Certificate Expiring" is
posted once when fewer than `UPTIME_CERT_DAYS` days are left (default
14) and again, as critical, below three days. A renewed certificate
clears it. The requests come from the server itself, so they test the
public address through the reverse proxy.

```text
UPTIME_URLS=https://jellyfin.example.org,https://photos.example.org
UPTIME_INTERVAL=300
UPTIME_TIMEOUT=15
UPTIME_CERT_DAYS=14
```

### Minecraft

`/minecraft` and the Minecraft monitor ask Java Edition servers for
their status the way the game's server list does (Server List Ping on
the game port; nothing has to be enabled on the server). `/minecraft`
shows one line per server: 🟢 or 🔴, name, version, players online and
the maximum, the names of who is playing, and the latency.

With `MINECRAFT_JOIN_ALERTS=true` the alert channel gets "Steve joined
Survival" and "Steve left Survival". A server only sends up to 12
player names, and none when names are hidden; it then gives no join
alerts. "Minecraft Server Down" is posted only for servers named in
`MINECRAFT_WATCH`, after two checks in a row without an answer, and
"Minecraft Server Recovered" when it answers again. Servers that are
stopped on purpose should not be in that list.

```text
# name=host:port, comma separated; the port defaults to 25565.
MINECRAFT_SERVERS=Survival=mc-survival:25565,Creative=mc-creative:25565
MINECRAFT_JOIN_ALERTS=false
# Servers that alert when they stop answering, comma separated names.
MINECRAFT_WATCH=
```

The address must be reachable from inside the bot's container: the
host's address and the published port of each itzg/minecraft-server
container, or the container name when they share a Docker network.

### Backup

`scripts/backup.sh` runs on the server from root's cron. It packs this
project (including `.env` and `data/`) and the folders in
`BACKUP_PATHS` into one `.tar.gz`, split into parts of
`BACKUP_PART_SIZE` (256M) in a folder per backup, skipping caches, transcodes, logs,
`MediaCover` and `metadata` folders. Containers in
`BACKUP_STOP_CONTAINERS` are stopped while it packs, so their databases
are copied consistently, and started again afterwards, also when
something fails. The last `BACKUP_KEEP_LOCAL` archives stay in
`BACKUP_LOCAL_DIR` (root only, because they contain passwords); the
newest is uploaded with rclone to `BACKUP_REMOTE`, where the last
`BACKUP_KEEP_REMOTE` are kept. The result goes to the watchdog's
webhook and into the weekly summary. If only the upload fails, the
local archive is kept; the message shows rclone's first error, and the
full output of the last run is in `<BACKUP_LOCAL_DIR>/rclone.log`.
Drafts left on Proton Drive by an interrupted upload are replaced.

Folders in `BACKUP_SYNC_PATHS` (large ones that change little, such as
game worlds) are left out of the archive and mirrored instead: while
the containers are stopped they are copied to `BACKUP_MIRROR_DIR`
(only changed files after the first run), then synced to
`<BACKUP_REMOTE>/mirror/current`. The first run uploads everything,
later runs only what changed, compared by checksum because Proton
Drive cannot store modification times. Files a run replaces or deletes
are moved to `<BACKUP_REMOTE>/mirror/previous/<date>`; the last
`BACKUP_KEEP_REMOTE` of those runs are kept.

Setup on the server:

1. rclone with its Proton Drive backend, configured as root (cron runs
   as root). The backend is an unofficial beta. Check that the
   installed rclone has it with `rclone help backends`; distribution
   packages can be too old, in which case install the current release
   from rclone.org.
2. `sudo rclone config`: new remote, name `proton`, storage
   `protondrive`, then your Proton username, password and, if used,
   the 2FA code.
3. The settings in `.env`, then a test run:
   `sudo bash scripts/backup.sh`
4. Cron, `sudo crontab -e`:

   ```text
   0 4 * * 0 bash /path/to/Aegis-Bot/scripts/backup.sh
   ```

```text
# Folders to back up, separated by spaces. This project is always included.
BACKUP_PATHS=
# Extra patterns or full paths to skip, separated by spaces, e.g. */Downloads/*
BACKUP_EXCLUDE=
# Folders mirrored instead of packed: full upload once, then only changes.
BACKUP_SYNC_PATHS=
# Local copy of those folders; needs as much space as they use.
BACKUP_MIRROR_DIR=/var/backups/aegis-bot/mirror
# Containers stopped while the archive is made, separated by spaces.
BACKUP_STOP_CONTAINERS=
# Size of each archive part; a failed upload only repeats one part.
BACKUP_PART_SIZE=256M
# Local archives: where, and how many to keep.
BACKUP_LOCAL_DIR=/var/backups/aegis-bot
BACKUP_KEEP_LOCAL=4
# rclone destination; empty keeps backups locally only.
BACKUP_REMOTE=proton:Backups/server
BACKUP_KEEP_REMOTE=8
```

The bot also watches `data/backup_status.json`: when the last
successful backup is older than `BACKUP_MAX_AGE_DAYS` (default 8) it
posts "No Recent Backup", saying whether the last run failed or no run
happened (a broken cron job), and "Backup Succeeded Again" afterwards.

To restore, join the parts and unpack them at the root; the archive
holds full paths:
`cat aegis-backup-<date>/aegis-backup-<date>.tar.gz.* | sudo tar -xzf - -C /`,
or add a folder name at the end to restore only that. From Proton
Drive, first `sudo rclone copy proton:Backups/server/aegis-backup-<date> ./aegis-backup-<date>`. A mirrored folder comes back with
`sudo rclone copy --links <BACKUP_REMOTE>/mirror/current/<path> /<path>`,
or from the local mirror when the disk survived.

### When the bot itself is down

No monitor can report that the bot has stopped. `scripts/watchdog.sh`
runs on the server from cron instead. It posts to a Discord webhook
when the container is not running or the bot has not written
`data/heartbeat` for ten minutes (hung, or disconnected from Discord),
and once more when the bot is back.

1. In Discord: the alert channel's settings > Integrations > Webhooks
   > New Webhook > Copy Webhook URL.
2. Add it to `.env`:

   ```text
   WATCHDOG_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```

3. Add a cron entry with `crontab -e`, as your own user (it must be
   allowed to run `docker`; no `sudo`):

   ```text
   */5 * * * * bash /path/to/Aegis-Bot/scripts/watchdog.sh
   ```

Whether "down" was already reported is kept in
`~/.local/state/aegis-bot/watchdog_state`, because the container owns
`data/` and your user cannot write there.

The webhook URL lets anyone who has it post in that channel; keep it in
`.env` only. The script cannot report a server that is off or has no
internet, because nothing runs then to send the message.

At startup the bot checks that qBittorrent, Jackett, iDRAC and every
Jellyfin server answer, and posts one alert naming any that do not.

Buttons stop working when the bot restarts. At startup the bot edits
the messages whose buttons were still active to say so, and marks a
Jellyfin download that was cut off as stopped. Requesting the download
again skips the files that were already copied.

## Container management

`/container` manages Docker containers through Portainer, which passes
Docker's own API through. Only the Discord users listed in
`PORTAINER_ALLOWED_USER_IDS` can use it, and every answer is private to
the person who asked.

- `list`, `info` and `logs` only read. `start` runs at once.
- `list` is paged (15 per page) with a Refresh button. Running
  containers come first. Dots show state: green healthy or running,
  yellow starting or paused, red unhealthy or exited with an error,
  white stopped. Exit codes 0, 137 and 143 count as stopped on purpose.
- `info` and every finished action show a details panel with Start,
  Restart, Stop, Logs and Refresh buttons. Buttons that do not apply
  (Start on a running container, Stop on a protected one) are disabled.
  Stop and Restart from the panel use the same confirmation.
- `stop` and `restart` ask for one confirmation.
- `kill` asks for a confirmation and then for the container name.
- `remove` asks for a confirmation, the container name and a final
  confirmation. A running container is refused, nothing is forced and
  volumes are never removed.
- Containers named in `PORTAINER_PROTECTED` cannot be stopped,
  restarted, killed or removed. The bot's own container and any
  `portainer/*` image are always protected too, because stopping them
  would cut off this control. The bot's own container is recognised by
  its hostname, which only works while the container keeps Docker's
  default hostname.
- Every confirmation step expires after 30 seconds.
- The container is looked up by name again just before the action, so
  one that was recreated since it was picked is left alone.
