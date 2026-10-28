# Development

How Aegis-Bot is put together, how to add a module, and what is easy to
get wrong. [README.md](../README.md) covers deployment and
[features.md](features.md) what every command does.

## Layout

```text
bot.py                 Entry point: picks the modules, loads their commands,
                       starts their monitors
core/                  Framework: modules registry, monitor manager and base
                       classes, notifications, maintenance, activity log,
                       startup check
modules/<name>/        One feature each (see below)
config/settings.py     Settings read from .env; only Discord values required
config/paths.py        DATA_DIR and files people may override
config/torrent_preferences.json   Default torrent ranking
utils/                 Logging, embeds, formatting, theme, expiring buttons
scripts/               Host-side scripts (watchdog, backup, rebuild, tidy_env)
tests/                 Checks that run the real code against local stand-ins
```

## Modules

A module is a folder in `modules/` with a `module.py`:

```text
modules/minecraft/
  module.py              MODULE = Module(...)
  commands/minecraft.py  slash commands: one discord.py extension per file
  minecraft_monitor.py   background monitor
  minecraft_service.py   the API client, no Discord code
```

```python
from core.modules import Module

MODULE = Module(
    name="minecraft",                  # must equal the folder name
    description="Minecraft Java server status, joins and down alerts.",
    required=("MINECRAFT_SERVERS",),   # off while any is empty
    monitors=("modules.minecraft.minecraft_monitor:MinecraftMonitor",),
)
```

Optional fields: `setup` (called once before commands load),
`startup_checks` (returns `{name: coroutine}`, see `checks.py` in
torrents, jellyfin and idrac) and `shutdown` (awaited when the bot
stops). They are written as `"package.module:attribute"` strings so a
module that is off is never imported.

`core/modules.py` finds every module, decides with `MODULES` and the
required settings which run, and logs the result. `modules.enabled(name)`
tells other code whether a module runs; the weekly summary uses it in
`WeeklySummaryMonitor.FIELDS` to leave out fields of modules that are
off.

Modules may import each other's code; `torrents` uses
`modules.jellyfin.jellyfin_service` to check the libraries before a
search, and handles a Jellyfin that is not configured. Removing a
module means deleting its folder, its rows in `FIELDS`, and anything
that imports it (`grep -r "modules.<name>"`).

### Adding a module

1. Create `modules/<name>/` with `__init__.py`, `module.py`, a
   `commands/` folder with an `__init__.py` if it has commands, and its
   service and monitor files.
2. Keep Discord code in `commands/` and views; keep API code in a
   service file without Discord imports.
3. A monitor subclasses `core.base_monitor.BaseMonitor`, takes the
   notification service as its only argument, may set `INTERVAL` and
   `TIMEOUT`, and keeps its state in `DATA_DIR / "<name>_state.json"`.
4. Post alerts through the notification service
   (`warning`, `critical`, `recovered`, `info`, `success`), never to a
   channel directly, so maintenance mode and mirrors apply.
5. Add every setting to `.env.example` in a section for the module,
   with its default (see "Settings and .env.example" below), and read
   it with `os.getenv` (or add it to `config/settings.py`). Never put
   addresses, names or credentials in code as defaults.
6. Add `tests/check_<name>.py` and its expected lines in
   `tests/run_all.py`. `tests/check_modules.py` checks every
   `module.py` automatically.

## Settings and .env.example

`.env.example` is the one list of settings. Whoever adds a setting adds
it there in the same change, and keeps the file tidy:

- One section per module, headed `Module: <name>. On when: <the
  required settings>`, in the style of the others. The settings a
  module needs to switch on must be inside its section and named in
  its heading.
- Every setting is shown with its default and a comment that says what
  it does and its unit. Empty means off or unset. Settings that belong
  together sit in one block, separated from the next by a blank line.
- A section with more than 12 settings is split with sub-headings, a
  line such as `# -- Search (Jackett) --` above each group. Split a
  section before it gets long, not after.
- A setting that is no longer read is removed from the file.

`tests/check_env_example.py` enforces this: it fails for a setting the
code or the host scripts read that is missing, a listed setting nothing
reads, a name listed twice, a module without a section, a heading that
does not name the module's required settings, and a long section without
sub-headings. A name the code mentions that is not a setting goes in
`IGNORE` in that check, with the reason.

## Checks

```bash
python -m compileall -q .
python tests/run_all.py
```

`tests/README.md` lists what each check covers. They run the real code
against local fake servers, so they catch logic errors, not surprises
from a real Discord or a real Jellyfin. Every check gets its own
temporary `DATA_DIR`.

A file with a syntax error does not stop the bot: `bot.py` logs the
failed command file and carries on without it, so the container looks
healthy while a command is missing. Read the startup log after a
change.

## Line endings

Some files use Windows line endings (CRLF), among them `bot.py`, most
of `utils/` and of the host and idrac modules. Keep each file's line
endings when editing; `git ls-files --eol | grep crlf` lists them.

## How it fits together

### Commands

`bot.py` loads every file in `modules/<name>/commands/` of a running
module as a discord.py extension. Each simple command is one Cog in
one file.

`/torrent` is a command group defined in `modules/torrents/commands/torrent.py`.
The `Torrent` Cog is assembled from mixins in `modules/torrents/torrent/`, one
per subcommand (`search.py`, `add.py`, `list.py`, `info.py`,
`status.py`, `watchlist.py`, `cleanup.py`, `speed.py`) plus
`formatting.py`. `approval.py` holds the optional admin approval;
`TorrentAddMixin._add()` takes a `requester`, so an approved torrent is
recorded for the person who asked, not the approver.
`TorrentSearchMixin._find_torrents()` is the Jackett search and
ranking on its own; the watchlist monitor reuses it through the cog.
Subcommands share the group's default permissions, so
`/torrent cleanup` checks for administrator itself. Interactive messages are `View`
classes: `views/` for torrent lists and controls, and
`jellyfin_download.py` for the Jellyfin download flow.

`/help` (`modules/general/commands/help.py`) reads the command tree at the moment it is
run. The area of each top-level command is in `AREAS` (unknown ones go
under "Other"). Administrator checks made inside a subcommand cannot be
read from the tree, so they are listed by hand in `ADMIN_ONLY` (hidden
from members) and `ADMIN_NOTES` (shown with a note); add a subcommand
there when it gets such a check.

### Services

- `QBittorrentService` instances all share one logged-in HTTP session.
  Callers still create an instance and call `close()` per action;
  `close()` does nothing and the session is closed when the bot stops.
  A 403 from qBittorrent triggers one new login and a retry.
- `JellyfinService` reads its servers from `JELLYFIN_<n>_*` in `.env`.
  Server 1 is the primary library. `find_matches()` asks all servers at
  once; `download_item()` streams one file to disk.
  `get_series_episodes()` returns every episode of a series in one
  request; `list_library()` lists every series or episode on a server
  for the daily comparison.
- `modules/jellyfin/jellyfin_compare.py` holds the comparison logic as pure
  functions: missing episodes, matching series between libraries,
  and scoring a copy's quality with `torrent_preferences.json`
  (`compare_copies()` gives every copy a score and one verdict).
- `modules/jellyfin/library_followup.py` runs after each completed Movies or
  Series torrent (started from `TorrentMonitor`, in the background):
  one library refresh per minute at most, a second one after the third
  check, then up to ten checks. `modules/torrents/release_name.py` reads the
  title, year, season and episode from the release name.
- `modules/jellyfin/subtitle_fetch.py` fetches missing movie subtitles through
  Jellyfin's `/Items/{id}/RemoteSearch/Subtitles` endpoints. Jellyfin
  answers the download with 204 even when the provider refused, so
  the item is read again (three times, ten seconds apart) to confirm.
  Only "nothing found" is remembered (`data/subtitle_fetch.json`, 30
  days). Called from `library_followup.py` before "Now in Jellyfin"
  and daily by `SubtitleMonitor`.
- `modules/jellyfin/movie_library.py` merges the movie lists of all servers
  for `/jellyfin movies` (`modules/jellyfin/commands/jellyfin.py`): one request per
  server through `/Users/{id}/Items` when `JELLYFIN_<n>_USER` is set
  (watched state from each item's `UserData.Played`), otherwise
  `/Items` without user data. A server returning no movies is treated
  as not answering. The merged list is cached for ten minutes for the
  title autocomplete (`movie_library.cached()` refreshes in the
  background); the command itself always reads fresh.
- `modules/wan/wan_recovery.py` restarts `WAN_RECOVERY_RESTART` through
  `PortainerService` after an outage; `WanMonitor` starts it in the
  background, also for outages below `WAN_OUTAGE_DURATION`.
- `modules/torrents/watchlist.py` stores watchlist entries in
  `data/watchlist.json`; `WatchlistMonitor` checks them every twelve
  hours, pausing five seconds between titles.
- `modules/idrac/hardware_service.py` reads `Power` and `Thermal` from
  iDRAC's Redfish chassis (`PowerSupplies`, `PowerControl`, `Fans`).
  Slots with State `Absent` are skipped. `Voltages` (board "power
  good" sensors, health only) are watched too: on the PowerEdge the
  bot was developed on, iDRAC reports `PowerMonitoringCapable = Disabled` and
  `PMBUSCapablePSU = Disabled`, so it has no power supply or power
  draw data at all. The field names follow the
  DMTF schema; they were checked against fakes, not a real iDRAC.
  `get_event_log()` reads the System Event Log
  (`/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries`, 50 entries
  a page, paged with `$skip` as Dell's own scripts do, at most 20
  pages) for `EventLogMonitor`.
- `modules/esxi/esxi_service.py` speaks the vSphere SOAP API with aiohttp
  (no pyVmomi): RetrieveServiceContent, Login, CreateContainerView,
  RetrievePropertiesEx, DestroyView, Logout. It reads
  `summary.name/capacity/freeSpace/accessible` of datastores and
  `name`, `runtime.powerState`, `snapshot` of VMs, without paging
  (fine for a single host). The `snapshot` tree (rootSnapshotList,
  nested childSnapshotList) is flattened into one list per VM with
  `createTime` as epoch seconds; `EsxiMonitor._snapshots` keeps the
  time of its last "Old Snapshot" warning per VM under `snapshots` in
  `data/esxi_monitor_state.json`. Checked against a fake host only.
- `JellyfinService.list_added_since()` lists the movies and
  episodes added to a server since a time, for the weekly summary's
  "New in Jellyfin". `/Items` has no filter on the date added
  (`minDateLastSaved` also moves on a metadata refresh), so it sorts
  by `DateCreated`, newest first, in pages of 200 until an older item
  appears, at most 2000 items.
- `modules/uptime/uptime_service.py` requests each URL in `UPTIME_URLS`
  without following redirects (below 500 is up) and reads an https
  certificate's expiry over a second TLS connection
  (`getpeercert()["notAfter"]`), because aiohttp may already have
  released the request's connection when the answer has no body.
  `UptimeMonitor` keeps per-URL failures and the last reported
  certificate level in `data/uptime_monitor_state.json`; it reads
  `UPTIME_INTERVAL` in `__init__`, since `.env` is loaded after the
  monitors are imported. Checked against local servers with test
  certificates only.
- `modules/minecraft/minecraft_service.py` speaks the Minecraft Java Edition
  Server List Ping with asyncio streams (no library): handshake with
  next state 1, status request, then ping/pong for the latency (the
  status round trip when a proxy closes before the pong). The packet
  format follows the mcstatus library. `/minecraft`
  (`modules/minecraft/commands/minecraft.py`) and `MinecraftMonitor` use `fetch_all()`,
  which asks every server in `MINECRAFT_SERVERS` at once, five seconds
  each. The monitor keeps its state in
  `data/minecraft_monitor_state.json`.
- `modules/fluxer/fluxer_webhook.py` mirrors every alert to Fluxer when
  `FLUXER_WEBHOOK_URL` is set. The module's setup registers `start()`
  with `notification_service.add_mirror()`, so it is called before
  every post to Discord; it schedules a background task and returns at
  once. The body is one rich embed as described in
  fluxerapp/fluxer `fluxer_docs/src/content/docs/http-api/webhooks.mdx`
  ("Execute webhook") and `messages.mdx` ("Rich embed input
  objects"). Discord markup (`<t:...>`, mentions, custom emoji) is
  rewritten by `readable()`, because Fluxer's docs do not describe it.
  Posts go one at a time (10 s limit, one retry after a short 429);
  failures are logged at most every five minutes and never raised. The
  message content (only mentions) is not sent.
- `core/activity_log.py` keeps 60 days of alerts (recorded in
  `NotificationService._send`) and Jellyfin copies, for the weekly
  summary. Torrent completions come from qBittorrent's
  `completion_on` instead.
- `core/maintenance.py` holds maintenance mode (`/maintenance`,
  `modules/general/commands/maintenance.py`) in `data/maintenance.json`.
  `NotificationService._send()` asks it before every alert: warning,
  recovered, info and success are not posted but still recorded in
  the activity log; critical gets "(during maintenance)". The start
  and end messages go through `_post()`, which nothing mutes. Expiry
  is checked on every alert and every minute by `MaintenanceMonitor`;
  `maintenance.end()` clears the state before anything is awaited, so
  the end message is posted once. The command creates its own
  `NotificationService(bot)`, which holds no state. Pass
  `exempt=True` to `info()` for reports that must not be muted (the
  weekly summary does).
- `modules/jellyfin/watch_stats.py` builds the summary's "Watched this week"
  from the Playback Reporting plugin on the primary Jellyfin server,
  through `JellyfinService.playback_query()` (POST
  `/user_usage_stats/submit_custom_query` with `ReplaceUserId`). One
  SQL query reads the week's rows of the plugin's `PlaybackActivity`
  table; totals are added up in Python. The plugin answers
  `{"colums", "results", "message"}` (its spelling), every cell a
  string, and lists no columns when there are no rows. Table, columns,
  the `"Series - s01e02 - Title"` episode names and the PlaybackMethod
  values ("Transcode (v:… a:…)", "DirectPlay", "na") were taken from
  the plugin's source (`Data/ActivityRepository.cs`,
  `Api/PlaybackReportingActivityController.cs`,
  `EventMonitorEntryPoint.cs`).
- `QBittorrentService.get_free_space()` reads `free_space_on_disk`
  from `/sync/maindata`: the disk of the default save path only.
- `modules/torrents/torrent_requesters.py` records which Discord user added
  each torrent, in `data/torrent_requesters.json`. Both torrent
  monitors use it to decide whom to mention.
- `ShareLinkService` turns a Google share link into title, year and
  category. It follows redirects only between Google's own hosts and
  makes one Wikidata query. Wikidata throttles bursts (HTTP 429 for
  about 100 seconds); the lookup then falls back to the title alone.
- `QBittorrentService.get_torrents_snapshot()` gives both torrent
  monitors the same list when it is at most five seconds old, so they
  share one request per cycle. Commands use `get_torrents()`, which is
  always fresh.
- `core/startup_check.py` runs the startup checks of every running
  module once (`modules/<name>/checks.py`: qBittorrent and Jackett,
  iDRAC, every Jellyfin server) and posts one alert listing any that
  did not answer.
- `PortainerService` (used by `modules/containers/commands/container.py`,
  `modules/wan/wan_recovery.py` and `modules/containers/watchtower_updates.py`)
  sends container calls through Portainer, which passes Docker's API
  through. Only list, inspect, logs and `logs_since()`, start, stop,
  restart, kill and remove are implemented, because the same token
  could do much more.
  `protection_reason()` keeps `aegis-bot`, Portainer and the names in
  `PORTAINER_PROTECTED` from being stopped, restarted, killed or
  removed; that check is in the service, not in the commands, so no
  command can skip it. It was checked against canned responses only,
  not against a real Portainer.
- `JackettService`, `JellyfinService`, `PortainerService` and
  `fluxer_webhook` read `.env` directly. The other services use `config.settings`.
- `modules/containers/watchtower_updates.py` gives the weekly summary the
  containers Watchtower recreated, parsed from its log
  (`logs_since()`, Docker timestamps). Within a session Watchtower logs
  every "Found new <image> image" before any "Creating /<name>", so
  each container is matched to an image through its current image in
  the container list, not by line order.
- `modules/torrents/torrent_action_tracker.py` wraps the pause and resume
  methods of `QBittorrentService` at startup, so the torrent health
  monitor can tell a pause made through the bot from one made
  elsewhere.

### Monitors

`MonitorManager` runs each monitor in its own loop with a timeout.
State shared between monitors is in `core/state.py` and saved to
`data/monitor_state.json`; the two torrent monitors keep their own
state files.

A monitor may set `INTERVAL` and `TIMEOUT` to override the global
values. `JellyfinNewContentMonitor` wakes every ten minutes, runs once
a day after `JELLYFIN_NEW_CONTENT_HOUR`, and remembers in
`data/jellyfin_new_content_state.json` what it has reported. Its alert
carries `NewContentView` (`modules/jellyfin/new_content_view.py`),
which compares the picked series again before offering the download.

`VpnMonitor` reads `/transfer/info` every two minutes and compares
`last_external_address_v4` with `state.wan["ip"]`, which `WanMonitor`
keeps current (it asks `WanService` itself only before that is set).
It assumes the bot's own container is not behind the VPN, so the WAN
address is the home line. `VpnMonitor.verdict()` is also used by
`/torrent status`.

`SeedCleanupMonitor` (weekly, `CLEANUP_REMINDER_*`) reuses
`seeded_enough()` from `modules/torrents/torrent/cleanup.py`, so it offers
exactly what `/torrent cleanup` marks ✅. Its `SeedCleanupView` is sent
through `notification_service.info(..., view=)`; it sets its own
`restart_note`, since there is no command to run again.

`HeartbeatMonitor` writes `data/heartbeat` every minute while the
bot is connected to Discord (finite latency). `scripts/watchdog.sh`
runs from cron on the host, outside the container, and is the only
part that can report the bot itself being down.

`scripts/backup.sh` runs from root's cron on the host. It writes
`data/backup_status.json` (readable by the bot, since root owns
`data/`), which the weekly summary shows as "Backup". The rclone Proton
Drive backend is an unofficial beta; Proton's own CLI cannot delete
old files, which is why rclone was chosen. Mirrored folders
(`BACKUP_SYNC_PATHS`) are synced with `--checksum` because that backend
cannot set modification times; without it every file would count as
changed. `tests/check_backup.py` replaces rclone with a small Python
fake, so the test needs no network.

`ThresholdMonitor` holds the logic for CPU and memory (sustained
warning, critical and recovery). `CPUMonitor` and `MemoryMonitor` only
supply the reading and the setting names.

`EventLogMonitor` remembers the `Created` time of the newest SEL
entry and the Ids with exactly that time
(`data/event_log_monitor_state.json`); an entry is new when it is
later, or equally late with another Id. It does not depend on the order
iDRAC lists entries in, nor on Ids, which restart after the log is
cleared.

Filesystem and temperature monitors store the state that was last
*reported*, together with the time of the last alert. That is what lets
the cooldown suppress flapping without losing a change that persists.

### Buttons and restarts

Every `View` derives from `ExpiringView` in `utils/open_messages.py`.
Assigning `view.message` records the message in
`data/open_messages.json`; the record is removed when the view times
out or stops. At startup the bot edits every message still recorded:
the buttons are removed and a note says to run the command again. A
Jellyfin download records its progress message the same way, with a
note that the download stopped. Ephemeral messages cannot be edited
after a restart and are not recorded.

A new view must derive from `ExpiringView` and assign `view.message`,
or its buttons fail silently after a restart.

### Logging

`utils/logger.py` sets up the bot's logger and also attaches the same
handlers to discord.py's logger. Without the second part, an error
raised inside a button or dropdown is discarded and the button simply
stops responding.

## Things that are easy to get wrong

- **Interactions must be answered within three seconds.** Defer or
  respond before any network call. A deferred button press shows no
  "thinking" indicator, so edit the message to say what is happening.
- **A message obtained from an interaction can only be edited for
  fifteen minutes.** Long-running work must fetch the message through
  its channel; see `JellyfinTransfer._durable_message()`.
- **Sizes are binary units** (KiB, MiB, GiB). Use `format_bytes()`
  from `utils/formatting.py`.
- **Blocking calls freeze the whole bot.** Run them with
  `asyncio.to_thread`, as `CPUService` and the Jellyfin download do.
- **qBittorrent state names** are `queuedDL`, `queuedUP`, `stoppedDL`,
  `stoppedUP` and so on. The single list is in
  `modules/torrents/torrent/constants.py`; use it instead of writing names out
  again.
- **File names from another Jellyfin server are untrusted.** Only the
  final path component is used; see `JellyfinService.media_filename()`.
- **The container runs as root.** Anything written to the library must
  be given the library's owner; see `JellyfinService._match_owner()`.

## Known limitations

- **Episodes are compared by number.** An episode Jellyfin could not
  number is ignored on the other server, and makes its season count
  as present in yours. Episodes listed by `/Items` were assumed to
  carry `SeriesId`, `ParentIndexNumber` and `IndexNumber` without
  asking for them in `Fields`; this was checked against fakes only.
- **Quality is compared for movies only.** Series episodes differ
  from file to file.
- **Only text subtitles in the preferred language are copied** as
  separate files, converted to `.srt` by the other server. Image-based
  subtitles and other languages are left behind.
- **Torrent results carry no subtitle information.** A marker based
  on release names was tried and removed: uploaders rarely mention
  subtitles, so it almost never appeared. Missing subtitles for movies
  are fetched afterwards through Jellyfin's Open Subtitles plugin
  (`modules/jellyfin/subtitle_fetch.py`), and for episodes with
  `SUBTITLE_SERIES`.
- **Title matching is deliberately strict.** A query must cover at
  least 60% of the words of the library title, and a short library
  title never matches a longer query. A miss only costs a normal
  torrent search. The rule is in `JellyfinService._titles_match()`.
- **The season picker stalled twice during testing**, with no error,
  and then worked. The cause was not found.
  Every Discord request in that flow is now logged with "sending" and
  "done" and limited to 30 seconds, so a repeat will show which step
  stopped.
- **`/torrent info` matches words, not the exact text.** Every word
  of the query must occur in the name after punctuation is turned
  into spaces, so "dark knight" finds "The.Dark.Knight.Rises". A typo
  still finds nothing.
- **The VPN leak check needs qBittorrent 5.1 or later.** Checked
  against qBittorrent's source: `last_external_address_v4` and `_v6`
  are in `/transfer/info` and `server_state` of `/sync/maindata` from
  release 5.1.0 (Web API 2.11.4) on, not in 5.0.x. The value is empty
  until libtorrent learns its external address from peers or
  trackers, and is the last address seen, so it can lag a tunnel
  failure. Older versions get only the "disconnected" check, logged
  once at startup. Only IPv4 is compared. The monitor was checked
  against a fake qBittorrent, not the real container.
- **"New in Jellyfin" was checked against fakes only.** The query
  parameters were read in Jellyfin's `ItemsController.cs`; sorting a
  real library by `DateCreated` and the date format were not seen on
  a real server. A file replaced by a better copy may count as new
  again.
- **The event log alerts are checked against a fake iDRAC only.**
  The SEL path is the one Dell's iDRAC-Redfish-Scripting uses for
  iDRAC 9, and the fields (`Id`, `Created`, `Severity` OK/Warning/
  Critical, `Message`, `SensorType`) are from the DMTF LogEntry
  schema; neither was seen on a real iDRAC. Not verified: which Severity
  iDRAC gives each SEL entry, whether `Created` carries a time zone
  (none is read as UTC, which would shift the shown time), and how
  the iDRAC clock and SEL clearing affect newness.
- **ESXi snapshots were checked against a fake host only.** The XML
  shape (VirtualMachineSnapshotInfo with repeated rootSnapshotList /
  childSnapshotList elements, `createTime` as xsd:dateTime) follows
  pyVmomi's type definitions, not a real ESXi 6.7 answer. A VM without
  snapshots is assumed to leave the `snapshot` property out; an
  unreadable `createTime` shows as "unknown age" and never warns.
- **"Watched this week" was checked against a fake plugin only.**
  The plugin stores DateCreated in the Jellyfin server's local time;
  the bot compares it with the start of the week in the container's
  time, so both should use the same time zone. PlayDuration is what
  the plugin records, which may include paused time.
- **Fluxer alerts were checked against a fake webhook only.** The
  payload follows Fluxer's docs; how Fluxer renders the embed (for
  example Markdown in fields) was not seen. Weekly summary lists that
  go to a Discord thread show "The full list is in the thread below."
  on Fluxer without that thread.
- **`/help` goes by the command defaults.** Permission changes made
  per role or user under Server Settings > Integrations are not visible
  to the bot, so `/help` may list a command Discord hides or the
  reverse. Checked against a fake command tree only.
- **Container updates are parsed from Watchtower's log text**, checked
  against its source (`pkg/container/client.go`, `cmd/root.go`) and a
  fake Portainer, not a real Watchtower. A container whose tag has
  since moved on (Docker lists an image ID) gets its image only when
  the session found one new image. Logs vanish when Watchtower's
  container is recreated, including by its own update.
- **The weekly cleanup suggestion was checked against fakes only**
  (`tests/check_seed_cleanup.py`). Its buttons last two days.
- **The daily report keeps one key per whole missing season.** More
  episodes of a season that was already reported as missing do not
  produce a new report.
- **Minecraft was checked against fake servers only.** Joins and
  leaves are read from the player sample in the status, which vanilla
  limits to 12 names and leaves empty or anonymous when names are
  hidden; then no join alerts are given. A player who joins and
  leaves within one minute is missed.
