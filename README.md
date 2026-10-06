# Aegis-Bot

Aegis-Bot is a self-hosted Python Discord bot designed for server management, infrastructure monitoring, torrent automation, and Jellyfin integration.

The project is primarily intended to run in Docker on a self-hosted server.

## Features

- Discord-based server management
- System and infrastructure monitoring
- Network and connectivity monitoring
- Storage and filesystem monitoring
- Hardware monitoring
- iDRAC / Redfish integration
- Wake-on-LAN
- Torrent searching and management
- Jackett integration
- qBittorrent integration
- Jellyfin library checking
- Jellyfin-based media downloading
- Docker-based deployment

## Project Structure

```text
Aegis-Bot/
├── bot.py
├── Dockerfile
├── requirements.txt
│
├── commands/
│   ├── ...
│   └── torrent/
│
├── config/
│   ├── __init__.py
│   └── torrent_preferences.json
│
├── models/
│   └── ...
│
├── monitors/
│   └── ...
│
├── redfish/
│   └── ...
│
├── services/
│   └── ...
│
├── utils/
│   └── ...
│
├── scripts/
│   └── rebuild.sh
│
├── README.md
├── HANDOVER.md
└── PROJECT_STATE.md
```

### Main Components

- `commands/` — Discord commands and user-facing bot functionality.
- `services/` — Integrations with external services and APIs.
- `monitors/` — System and infrastructure monitoring functionality.
- `models/` — Data models used throughout the application.
- `redfish/` — Redfish / iDRAC-related functionality.
- `utils/` — Shared utility functions.
- `config/` — Non-sensitive configuration and application defaults.
- `scripts/` — Deployment and maintenance scripts.

## Configuration

Private configuration is intentionally not included in this repository.

The following files are excluded from Git:

```text
.env
config/settings.py
.venv/
data/*.json
```

These files contain environment-specific configuration, credentials, runtime state, or other private information.

When setting up a development environment, the required configuration must be recreated locally.

## Docker

Aegis-Bot is designed to run in Docker.

The production container is currently named:

```text
aegis-bot
```

The project is mounted into the container as:

```text
/app
```

The host storage is also available to the container for integrations that require access to files on the server.

The normal rebuild process is:

```bash
bash scripts/rebuild.sh
```

## Development Guidelines

Aegis-Bot is an existing production application. Changes should therefore be incremental and targeted.

### General Rules

- Preserve existing functionality.
- Avoid unnecessary rewrites.
- Do not replace working integrations without a clear reason.
- Keep changes limited to the feature being developed.
- Maintain compatibility with the existing Docker deployment.
- Keep Discord/UI logic inside the appropriate command modules.
- Keep external service/API logic inside `services/` where practical.
- Do not commit credentials, API keys, tokens, or private configuration.
- Test changes before deploying them to the production container.

### External Integrations

Existing integrations should be treated as dependencies of the application.

In particular, changes to torrent functionality should preserve the existing:

- Jackett integration
- qBittorrent integration
- Torrent search flow
- Existing Discord UI

## Torrent Search

Torrent searching is handled through the torrent command system.

The current workflow integrates with Jellyfin before performing a normal torrent search.

For supported media categories:

1. Search Jellyfin for the requested title.
2. If the media already exists in Jellyfin, inform the user.
3. Allow the user to explicitly continue with a torrent search.
4. Otherwise continue with the existing Jackett torrent search workflow.

The Jellyfin check must not remove or bypass the existing torrent-search functionality.

## Jellyfin Integration

The project currently has a Jellyfin service used to search configured Jellyfin servers.

The first Jellyfin integration is already functional.

The intended architecture supports multiple Jellyfin servers, allowing the bot to determine where requested media is available before starting a torrent search.

### Jellyfin 1

Jellyfin 1 is the primary media library.

When media is already available there, the bot reports that it is available and provides an option to continue with a torrent search if desired.

### Jellyfin 2

The next development stage is to use Jellyfin 2 as an additional source for media.

The intended workflow is:

```text
User searches for media
        │
        ▼
Check Jellyfin 1
        │
   ┌────┴────┐
   │         │
 Found    Not found
   │         │
   ▼         ▼
 Show     Check Jellyfin 2
available       │
           ┌────┴────┐
           │         │
        Found     Not found
           │         │
           ▼         ▼
   Download from   Torrent
   Jellyfin 2      search
```

If media exists in Jellyfin 2, the user should be given the option to download it from Jellyfin 2 into the primary Jellyfin 1 library.

### Movies

Movies downloaded from Jellyfin 2 should be placed in the Jellyfin 1 Movies library while preserving the original media filename.

Target structure:

```text
/media/Movies/<Movie Name>/<original media filename>
```

After the download completes, Jellyfin 1 should be refreshed so the media becomes available in the primary library.

### Series

For series, the user should be able to select a season.

Only the selected season should be downloaded.

Episodes should be placed in the Jellyfin 1 Series library while preserving their original filenames.

Target structure:

```text
/media/Series/<Series Name>/Season XX/<original episode filename>
```

After the download completes, Jellyfin 1 should be refreshed.

### Music

Music is currently outside the scope of the Jellyfin download workflow.

Do not implement Jellyfin 2 music downloading unless explicitly requested.

## API Design

External service communication should remain separated from Discord command handling.

For example:

```text
Discord command
      │
      ▼
Command logic
      │
      ▼
Service layer
      │
      ▼
External API
```

This keeps the bot commands easier to maintain and allows the service integrations to be tested independently.

## Testing

Before deploying changes:

1. Verify the Python code starts correctly.
2. Verify the Docker container starts correctly.
3. Test the affected Discord command.
4. Verify existing functionality still works.
5. Check container logs for errors.
6. Test both successful and failure paths for external API operations.

For Jellyfin-related changes, test at minimum:

- Media found in Jellyfin 1.
- Media not found in Jellyfin 1.
- Media found in Jellyfin 2.
- Media not found in either server.
- Movie download.
- Series season selection.
- Series season download.
- Jellyfin library refresh.
- API/network failure.
- Invalid or missing media data.

## Development Documentation

Additional development information is maintained in:

- `HANDOVER.md` — development handover and implementation guidance.
- `PROJECT_STATE.md` — current implementation state and outstanding work.

These files are intended to give another developer or AI coding assistant enough context to continue development without having to reconstruct the project history.

## License

No open-source license has currently been selected for this project.
