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
| `/cpu`, `/memory`, `/filesystems`, `/network` | Live readings from the host | Everyone |
| `/storage`, `/temperature`, `/idrac` | RAID, sensors and iDRAC details through Redfish | Everyone |
| `/server` | Server model, service tag, BIOS and OS through Redfish | Administrators |
| `/wan` | Public IP address | Administrators |
| `/wol` | Wakes the configured computer, or reports that it is already awake | Administrators |
| `/ping`, `/hello` | Checks that the bot responds | Everyone |

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
        |   seasons ....................... "<server> has more seasons"
        |                                   [Download missing season from <server>]
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
- One download runs at a time.

A missing season of a series that is already in the primary library
is copied into the folder that series already uses.

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
only the release name, not the files inside. Fetching subtitles for the
library is left to Jellyfin, for example with its Open Subtitles plugin.

The other server's API key must be allowed to download media.

## Monitoring

Monitors run in the background and post to the alert channel when a
state changes.

| Monitor | Watches | Interval |
|---|---|---|
| CPU, Memory | Sustained high usage, with separate recovery thresholds | 60 s |
| Filesystem | Usage of the host's mounted filesystems | 60 s |
| Temperature | iDRAC temperature sensors | 60 s |
| Storage | RAID controller, virtual disks and physical drives | 60 s |
| WAN | Internet connectivity and public IP changes | 60 s |
| Torrent | Completed downloads | 10 s |
| Torrent health | Stalled, errored, stopped and resumed torrents | 10 s |

Filesystem and temperature alerts for the same component are at least
`ALERT_COOLDOWN` seconds apart, so a value hovering on a threshold does
not alert on every crossing. A component becoming critical is always
reported immediately.

Torrent notifications (completed, stalled, error) mention the person
who added the torrent through the bot. Torrents added some other way,
for example in qBittorrent itself, mention `DISCORD_USER_ID`.

Monitor state is kept in `data/` so a restart does not repeat alerts.

At startup the bot checks that qBittorrent, Jackett, iDRAC and every
Jellyfin server answer, and posts one alert naming any that do not.

Buttons stop working when the bot restarts. At startup the bot edits
the messages whose buttons were still active to say so, and marks a
Jellyfin download that was cut off as stopped. Requesting the download
again skips the files that were already copied.

## Configuration

Configuration lives in two untracked files.

### `config/settings.py`

Copy `config/settings.example.py` to `config/settings.py`. It reads
everything from `.env` and contains no addresses or credentials itself.
The comments in that file describe every value. Missing required values
are listed in the log at startup.

### `.env`

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

# Library folders as seen inside the container. Setting these enables
# downloading from another Jellyfin server.
JELLYFIN_MOVIES_DIR=/media/Movies
JELLYFIN_SERIES_DIR=/media/Series

# Preferred subtitle language: a name or code such as english, en, nl.
SUBTITLE_LANGUAGE=english

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
services/              Clients for qBittorrent, Jackett, Jellyfin, GPTWOL, Redfish data
monitors/              Background checks and their persisted state
models/                Small data classes returned by services
redfish/               HTTP client for the iDRAC Redfish API
utils/                 Logging, embeds, formatting, theme
config/                Settings template and torrent ranking preferences
scripts/rebuild.sh     Restart the container and show its startup log
tests/                 Checks that run the bot's code against local stand-ins
```

Development notes, working method and known limitations are in
`HANDOVER.md`.

## License

No open-source license has been selected for this project.
