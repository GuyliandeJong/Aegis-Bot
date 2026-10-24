# Aegis-Bot

Aegis-Bot is a self-hosted Discord bot for a home server. It posts
alerts when something needs attention and answers slash commands about
the server, its containers and its media: hardware health through a
Dell iDRAC, ESXi virtual machines, Docker containers through Portainer,
torrents in qBittorrent, Jellyfin libraries, Minecraft servers and
more.

Every feature is a module. A module switches itself on when its
settings are filled in, so you only configure what you use. It serves
one Discord server and runs in Docker.

## Modules

| Module | What it does | Commands | On when these are set |
|---|---|---|---|
| general | Help, maintenance mode, weekly summary, heartbeat | `/help`, `/maintenance`, `/ping`, `/hello` | Always |
| host | CPU, memory, disk and network of the machine the bot runs on | `/cpu`, `/memory`, `/filesystems`, `/network` | Always |
| wan | Internet outages, public IP changes, container restarts after an outage | `/wan` | Always |
| idrac | Dell server hardware: temperatures, RAID, power, fans, event log | `/server`, `/idrac`, `/storage`, `/temperature` | `IDRAC_HOST`, `IDRAC_USERNAME`, `IDRAC_PASSWORD` |
| esxi | ESXi datastores, virtual machines and old snapshots | `/esxi` | `ESXI_HOST`, `ESXI_USERNAME`, `ESXI_PASSWORD` |
| containers | Docker containers through Portainer: control, crash alerts, Watchtower updates | `/container` | `PORTAINER_URL`, `PORTAINER_API_KEY`, `PORTAINER_ENDPOINT_ID` |
| torrents | qBittorrent: search (Jackett), add, list, approvals, watchlist, seeding cleanup, VPN check | `/torrent` | `QBITTORRENT_HOST`, `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` |
| jellyfin | Jellyfin: movie lookup with watched state, now playing, new content, subtitles, copies between servers | `/jellyfin` | `JELLYFIN_1_URL`, `JELLYFIN_1_API_KEY` |
| wol | Wake and sleep a computer through GPTWOL | `/wol` | `GPTWOL_HOST`, `GPTWOL_TARGET_IP`, `GPTWOL_TARGET_MAC` |
| minecraft | Minecraft Java server status, joins, down alerts | `/minecraft` | `MINECRAFT_SERVERS` |
| uptime | Public URLs answering and their TLS certificates | — | `UPTIME_URLS` |
| backup | Alerts when the backup script has not succeeded recently | — | `BACKUP_PATHS` |
| fluxer | Mirrors every alert to a Fluxer channel | — | `FLUXER_WEBHOOK_URL` |

What each command and alert does is described in
[docs/features.md](docs/features.md).

## Quick start

### 1. Create the Discord bot

1. In the [Discord developer portal](https://discord.com/developers/applications),
   create an application, open **Bot**, and copy its token
   (**Reset Token**). No privileged intents are needed.
2. Under **OAuth2 > URL Generator**, tick the scopes `bot` and
   `applications.commands`, and the bot permissions **View Channels**,
   **Send Messages**, **Embed Links**, **Read Message History**,
   **Create Public Threads** and **Send Messages in Threads**. Open the
   generated URL to invite the bot to your server.
3. In Discord, turn on **Developer Mode** (User Settings > Advanced).
   Right-click your server and choose **Copy Server ID**, then
   right-click the channel for alerts and choose **Copy Channel ID**.

### 2. Run it

In an empty folder, put a copy of
[docker-compose.example.yml](docker-compose.example.yml) named
`docker-compose.yml`, and a copy of [.env.example](.env.example) named
`.env`. Both are also attached to every
[release](../../releases/latest), as `docker-compose.yml` and
`env.example`. In `.env`, set:

```bash
DISCORD_TOKEN=<the bot token>
GUILD_ID=<the server ID>
ALERT_CHANNEL_ID=<the channel ID>
```

Then:

```bash
docker compose up -d
docker compose logs -f aegis-bot
```

The log lists every module as on or off, with the settings an off
module is missing:

```text
Modules:
  general     on
  host        on
  torrents    off: set QBITTORRENT_HOST, QBITTORRENT_USERNAME, QBITTORRENT_PASSWORD
```

Fill in the settings of the modules you want, in their section of
`.env`, and restart with `docker compose up -d`.

To build the image yourself instead of pulling it, clone this
repository and replace the `image:` line in `docker-compose.yml` with
`build: .`.

## Configuration

Everything is set in `.env`. [.env.example](.env.example) has one
section per module, with the default of every setting and what it
does.

- **Which modules run**: `MODULES=auto` (the default) runs every module
  whose settings are set. `MODULES=torrents,jellyfin` runs only those;
  `MODULES=auto,-wan` runs everything set up except `wan`. The general
  module always runs.
- **Addresses** are as seen from inside the bot's container: a service
  on the same Docker network is reached by its container name, one on
  the host by the host's LAN address.
- **Time zone**: `TZ` in `docker-compose.yml` sets the time used for
  schedules (weekly summary, daily runs) and shown in alerts.
- **Data**: state, logs and the buttons of open messages are kept in
  `./data` (mounted at `/app/data`). Keep it when updating.
- **Torrent ranking**: copy `config/torrent_preferences.json` to
  `data/torrent_preferences.json` and edit it to change which releases
  `/torrent search` prefers; see [docs/features.md](docs/features.md).
- **Who may run what**: administrator commands use Discord's
  administrator permission as their default. Change it per command,
  role or user under Server Settings > Integrations > Aegis-Bot.

Passwords, tokens and API keys are never logged, and normal log lines
leave out addresses; an error from a service that cannot be reached
may still name its address. `scripts/tidy_env.py` reports which
settings an existing `.env` has, by name only, and can sort it into the
layout of `.env.example`.

## Updating

```bash
docker compose pull
docker compose up -d
```

## Running without Docker

Python 3.13 or later:

```bash
pip install -r requirements.txt
DATA_DIR=./data python bot.py
```

`DATA_DIR` is where state and logs go (default `/app/data`). The host
module looks for the host's disks under `/host`, the read-only mount in
the container; without it, disk usage is only shown when `DATA_DIR` is
a mount point itself.

## Scripts for the host

Optional, run on the host from cron, outside the bot:

| Script | What it does |
|---|---|
| `scripts/watchdog.sh` | Posts to a Discord webhook when the bot's container is down or the bot stopped writing its heartbeat |
| `scripts/backup.sh` | Packs configured folders into an archive, uploads it with rclone, and reports to Discord and the weekly summary |
| `scripts/rebuild.sh` | Recreates the container and shows its startup log (DockSTARTer or docker compose) |
| `scripts/tidy_env.py` | Checks and sorts a `.env` without printing any value |

Each script explains its setup at the top; the backup is described in
[docs/features.md](docs/features.md#backup).

## Documentation

- [docs/features.md](docs/features.md): every command, alert and
  option in detail.
- [docs/development.md](docs/development.md): how the code fits
  together, how to add a module, and how to run the checks.
- [SECURITY.md](SECURITY.md): reporting a security problem.
- [CONTRIBUTING.md](CONTRIBUTING.md): changes and pull requests.

## License

[MIT](LICENSE).
