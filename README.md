# Aegis-Bot

Aegis-Bot is a self-hosted Discord bot for one home server. It reports
on the server's hardware and health, sends alerts when something needs
attention, and manages media: searching and adding torrents, and copying
movies and series from another Jellyfin server.

It runs in Docker and serves a single Discord server (guild).

## Commands

| Command | What it does | Who can use it |
|---|---|---|
| `/torrent search` | Checks Jellyfin first, then searches Jackett and adds the chosen result to qBittorrent | Everyone |
| `/torrent add` | Adds a magnet link to qBittorrent | Everyone |
| `/torrent list` | Lists torrents, with filter, sort and live refresh | Everyone |
| `/torrent info` | Details and controls for one torrent | Everyone |
| `/torrent status` | qBittorrent version and transfer totals | Everyone |
| `/torrent watchlist` | Titles you are waiting for; remove them | Everyone (own list) |
| `/torrent cleanup` | Finished torrents by ratio and seeding time; remove them | Administrators |
| `/torrent speed` | Show qBittorrent's speed limits; switch between normal and slow | Everyone sees; administrators switch |
| `/cpu`, `/memory`, `/filesystems`, `/network` | Live readings from the host | Everyone |
| `/storage`, `/temperature`, `/idrac` | RAID, sensors and iDRAC details through Redfish | Everyone |
| `/server` | Server model, service tag, BIOS and OS through Redfish | Administrators |
| `/esxi` | ESXi datastores (usage) and virtual machines (running or not) | Administrators |
| `/wan` | Public IP address | Administrators |
| `/wol` | Wakes the configured computer, or reports that it is already awake | Administrators |
| `/jellyfin movies` | Looks up a movie on all Jellyfin servers, with watched state; the title has autocomplete | Everyone (private reply) |
| `/container` | Lists, inspects and controls Docker containers through Portainer: `list`, `info`, `logs`, `start`, `stop`, `restart`, `kill`, `remove` | Only the users in `PORTAINER_ALLOWED_USER_IDS` |
| `/ping`, `/hello` | Checks that the bot responds | Everyone |

`/wan`, `/server`, `/idrac`, `/network` and `/esxi` reply privately: only the
person who ran the command sees the answer.

"Administrators" is Discord's administrator permission, set as the
command default. It can be changed per user or role in Discord under
Server Settings > Integrations.

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
chosen on the others. Remove them with or without their files;
removing files asks for confirmation. Only administrators can use it,
and the reply is visible to them only.

```text
SEED_RATIO_TARGET=2     # a ratio counted as seeded enough
SEED_DAYS_TARGET=14     # or this many days of seeding
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
|---|---|---|
| CPU, Memory | Sustained high usage, with separate recovery thresholds | 60 s |
| Filesystem | Usage of the host's mounted filesystems | 60 s |
| Temperature | iDRAC temperature sensors | 60 s |
| Storage | RAID controller, virtual disks and physical drives | 60 s |
| Hardware | Power supply and fan health through iDRAC | 60 s |
| WAN | Internet connectivity and public IP changes | 60 s |
| Jellyfin new content | New seasons and episodes on the other servers | Once a day |
| Subtitles | Missing movie subtitles, a few per run | Once a day |
| Watchlist | Titles people are waiting for | Twice a day |
| Weekly summary | Disk growth with a "full in" estimate, completed torrents, Jellyfin copies, alerts | Once a week |
| Heartbeat | Writes `data/heartbeat` for the watchdog script | 60 s |
| ESXi | Datastore usage, virtual machines stopping, host reachable | 5 min |
| Torrent | Completed downloads | 10 s |
| Torrent health | Stalled, errored, stopped and resumed torrents | 10 s |

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

The weekly summary is posted in the alert channel:

```text
WEEKLY_SUMMARY=true          # false turns it off
WEEKLY_SUMMARY_DAY=sunday
WEEKLY_SUMMARY_HOUR=10       # container time
```

Disk growth is measured from one summary to the next, so the first
summary only shows current usage. Completed torrents and Jellyfin copies are
listed in full, with readable titles ("Inception (2010)", "Fallout —
S02E03") and their size. When a list does not fit in the summary, the
bot opens a thread under it with the complete list; it needs the
"Create Public Threads" permission in the alert channel for that, and
otherwise posts the list below the summary. The summary also counts internet
outages (number, total and longest) confirmed by the WAN monitor.

Physical drives alert when iDRAC predicts a failure, and SSDs also when
their remaining write endurance drops to `SSD_LIFE_WARNING` percent
(default 10). Hard disks do not report this.

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

```text
ESXI_HOST=
ESXI_USERNAME=
ESXI_PASSWORD=
ESXI_DATASTORE_WARNING=85
ESXI_DATASTORE_CRITICAL=95
# Virtual machines not to watch, comma separated.
ESXI_IGNORE_VMS=
```

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
BACKUP_REMOTE=proton:Backups/home
BACKUP_KEEP_REMOTE=8
```

To restore, join the parts and unpack them at the root; the archive
holds full paths:
`cat aegis-backup-<date>/aegis-backup-<date>.tar.gz.* | sudo tar -xzf - -C /`,
or add a folder name at the end to restore only that. From Proton
Drive, first `sudo rclone copy proton:Backups/home/aegis-backup-<date> ./aegis-backup-<date>`. A mirrored folder comes back with
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

## Configuration

Configuration lives in two untracked files.

### `config/settings.py`

Copy `config/settings.example.py` to `config/settings.py`. It reads
everything from `.env` and contains no addresses or credentials itself.
The comments in that file describe every value. Missing required values
are listed in the log at startup.

### `.env`

`.env.example` lists every setting with its default, grouped and
commented. To sort an existing `.env` into that layout:

```bash
python3 scripts/tidy_env.py
```

prints, by name only and never with values, which settings are set
twice, unknown, required but empty, or equal to their default. With
`--write` it creates `.env.tidy` (same values, template order, unset
settings commented out with their default, unknown ones at the end),
checks it means exactly the same as `.env`, and leaves `.env` alone.
`--write --drop-defaults` also comments out settings equal to their
default, unless the local `config/settings.py` has another default for
them.

Required:

```text
DISCORD_TOKEN=
GUILD_ID=
ALERT_CHANNEL_ID=

IDRAC_HOST=
IDRAC_USERNAME=
IDRAC_PASSWORD=

QBITTORRENT_HOST=
QBITTORRENT_PORT=
QBITTORRENT_USERNAME=
QBITTORRENT_PASSWORD=

JACKETT_URL=
JACKETT_API_KEY=

GPTWOL_HOST=
GPTWOL_TARGET_IP=
GPTWOL_TARGET_MAC=
```

Optional:

```text
# Mentioned for torrents that were not added through the bot.
DISCORD_USER_ID=

# Jellyfin servers, numbered from 1. Server 1 is the primary library.
JELLYFIN_1_URL=
JELLYFIN_1_API_KEY=
JELLYFIN_1_NAME=
JELLYFIN_2_URL=
JELLYFIN_2_API_KEY=
JELLYFIN_2_NAME=
# Jellyfin user whose watched state /jellyfin movies shows, per server.
# Empty: that server's movies are listed without watched state.
JELLYFIN_1_USER=
JELLYFIN_2_USER=

# Library folders as seen inside the container. Setting these enables
# downloading from another Jellyfin server.
JELLYFIN_MOVIES_DIR=/media/Movies
JELLYFIN_SERIES_DIR=/media/Series

# Preferred subtitle language: a name or code such as english, en, nl.
SUBTITLE_LANGUAGE=english
# Fetch missing movie subtitles through Jellyfin's Open Subtitles plugin.
SUBTITLE_AUTO_DOWNLOAD=true
# Movies tried in the daily run (0 turns it off), and its hour (container time).
SUBTITLE_DAILY_LIMIT=10
SUBTITLE_HOUR=6
# Prefer subtitles for the hearing impaired.
SUBTITLE_HEARING_IMPAIRED=false

# Daily report of new seasons and episodes on the other servers.
JELLYFIN_NEW_CONTENT_ALERTS=true
JELLYFIN_NEW_CONTENT_HOUR=10

# What /torrent cleanup counts as seeded enough.
SEED_RATIO_TARGET=2
SEED_DAYS_TARGET=14

# true: torrents from non-administrators wait for an administrator's approval.
REQUEST_APPROVAL=false

# Refresh Jellyfin and say "Now in Jellyfin" after a torrent completes.
JELLYFIN_REFRESH_ON_COMPLETE=true

# Weekly summary in the alert channel.
WEEKLY_SUMMARY=true
WEEKLY_SUMMARY_DAY=sunday
WEEKLY_SUMMARY_HOUR=10

# ESXi host (optional). Use a user with the read-only role.
ESXI_HOST=
ESXI_USERNAME=
ESXI_PASSWORD=
ESXI_DATASTORE_WARNING=85
ESXI_DATASTORE_CRITICAL=95
ESXI_IGNORE_VMS=

# Warn when an SSD has this percentage of its rated life left, or less.
SSD_LIFE_WARNING=10

# Container management through Portainer (/container). The address must
# be reachable from inside the bot's container. Create the key under
# My account > Access tokens in Portainer.
PORTAINER_URL=
PORTAINER_API_KEY=
PORTAINER_ENDPOINT_ID=
# Comma separated Discord user IDs. Empty means nobody.
PORTAINER_ALLOWED_USER_IDS=
# Comma separated container names that cannot be stopped, restarted,
# killed or removed from Discord.
PORTAINER_PROTECTED=
# false for a self-signed certificate.
PORTAINER_VERIFY_TLS=true

# Restart these containers (comma separated) through Portainer after an
# internet outage, e.g. newt for Pangolin. Empty: nothing is restarted.
WAN_RECOVERY_RESTART=
# Only after an outage of at least this many seconds, and wait this long
# after the connection is back before restarting.
WAN_RECOVERY_MIN_OUTAGE=60
WAN_RECOVERY_DELAY=30

# Used only by scripts/watchdog.sh on the server.
WATCHDOG_WEBHOOK_URL=

# Jackett indexer IDs to search, comma separated. Empty: rutracker
# (RuTracker.org, which needs an account in Jackett), thepiratebay, 1337x.
JACKETT_INDEXERS=
JACKETT_TIMEOUT=
GPTWOL_TARGET_NAME=
ALERT_COOLDOWN=1800
```

Thresholds and durations for the monitors can also be set in `.env`;
see `config/settings.example.py` for their names and defaults.

`config/torrent_preferences.json` controls how search results are
ranked per category: preferred resolutions, codec scores and terms to
avoid.

It also holds two uploader lists, which count for the categories in
`uploader_categories` (default `["PC"]`: software you run yourself). Jackett passes
the uploader's name for RuTracker.org, The Pirate Bay and 1337x, but no
reputation, so the lists are your own judgement:

- `trusted_uploaders` (InsaneRamZes, FitGirl, DODI): `trusted_uploader_score`
  (30) is added and the result shows ⭐ before the name.
- `blocked_uploaders`: `blocked_uploader_score` (-100) is added, which
  puts the release at the bottom.

Names are matched without regard to capitals, on the uploader only: a
group name such as "FitGirl Repack" in a title from someone else does
not count. Search results show the uploader as "by name".

## Docker

The container is named `aegis-bot`. The project folder is mounted at
`/app`, so source changes only need a restart, not an image rebuild.

Required mounts:

```yaml
volumes:
  # Read-only view of the host for monitoring.
  - /:/host:ro

  # The project itself.
  - /path/to/Aegis-Bot:/app

  # Read-write access to the media library, for Jellyfin downloads.
  - /path/to/media:/media
```

The media mount must be read-write. A read-only mount makes every
download fail with a "mounted read-write" message.

Restart after a change:

```bash
bash scripts/rebuild.sh
```

The script restarts the container, waits until the bot reports that it
has started, and prints the startup log. Check that every command file
is listed as loaded.

Logs:

```bash
docker logs --since 10m aegis-bot
```

The same output is kept in `data/logs/aegis.log`.

## Project structure

```text
bot.py                 Entry point: loads commands, starts monitors
commands/              One file per slash command
commands/torrent/      The /torrent group, its views and the Jellyfin download
services/              Clients for qBittorrent, Jackett, Jellyfin, GPTWOL, Portainer, Redfish data
monitors/              Background checks and their persisted state
models/                Small data classes returned by services
redfish/               HTTP client for the iDRAC Redfish API
utils/                 Logging, embeds, formatting, theme
config/                Settings template and torrent ranking preferences
scripts/rebuild.sh     Restart the container and show its startup log
scripts/watchdog.sh    Cron job: alert through a webhook when the bot is down
scripts/backup.sh      Cron job: back up configs and upload them to Proton Drive
scripts/redfish_power_check.py  Show what iDRAC reports about power supplies
scripts/redfish_explore.py      List every power-related value on iDRAC's Redfish pages
tests/                 Checks that run the bot's code against local stand-ins
```

Development notes, working method and known limitations are in
`HANDOVER.md`.

## License

No open-source license has been selected for this project.
