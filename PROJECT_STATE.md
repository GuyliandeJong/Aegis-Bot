# Aegis-Bot Project State

This document describes the current implementation state of Aegis-Bot and the work that is currently in progress.

It is intended to provide a quick overview of what is already working, what is being developed, and what still needs to be implemented.

---

## 1. Repository State

The project is currently maintained in a private GitHub repository.

The repository contains the current Aegis-Bot source code and development documentation.

Private/runtime files are intentionally excluded from Git.

Excluded files include:

```text
.env
config/settings.py
.venv/
data/*.json
```

The repository currently contains:

- `README.md`
- `HANDOVER.md`
- `PROJECT_STATE.md`

These documentation files describe the project and provide development context for future work.

## 2. Application State

Aegis-Bot is an existing Docker-based Python Discord bot.

The production container is:

```text
aegis-bot
```

The application source is mounted into the container at:

```text
/app
```

The normal rebuild process is:

```bash
bash scripts/rebuild.sh
```

The application already contains functionality for:

- Discord commands
- Server management
- System monitoring
- Storage monitoring
- Network monitoring
- Hardware monitoring
- iDRAC / Redfish
- Wake-on-LAN
- Torrent searching
- Jackett
- qBittorrent
- Jellyfin

## 3. Torrent Search State

The torrent search system is already functional.

The existing workflow uses Jackett for torrent searches and integrates with the existing qBittorrent setup.

Jellyfin availability checking has been added to the beginning of the torrent search workflow.

The important requirement is that Jellyfin availability must not prevent the user from manually choosing a torrent search.

## 4. Jellyfin 1 State

The first Jellyfin integration is working.

The Jellyfin service is located at:

```text
services/jellyfin_service.py
```

The torrent search implementation is located at:

```text
commands/torrent/search.py
```

The current workflow for Movies and Series is:

```text
/torrent search <title>
        │
        ▼
Check Jellyfin
        │
   ┌────┴────┐
   │         │
 Found    Not found
   │         │
   ▼         ▼
 Show      Continue to
available  torrent search
   │
   ▼
Search for torrent
```

When media is already available in Jellyfin, the bot displays an availability message and provides a:

```text
Search for torrent
```

option.

Selecting this option continues with the normal torrent search.

This behavior is already implemented and should be preserved.

## 5. Jellyfin 2 State

Jellyfin 2 is the current development focus.

The intended behavior is:

```text
Check Jellyfin 1
        │
        ├── Found
        │     │
        │     └── Existing Jellyfin behavior
        │
        └── Not found
              │
              ▼
        Check Jellyfin 2
              │
          ┌───┴────┐
          │        │
       Found     Not found
          │        │
          ▼        ▼
     Download    Torrent
     from JF2    search
```

The Jellyfin 2 implementation is not yet considered complete.

The required user interface when media is found in Jellyfin 2 is:

```text
Download from Jellyfin 2
Search for torrent
```

## 6. Jellyfin 2 Movie State

The intended movie workflow is:

1. Search Jellyfin 1.
2. If not found, search Jellyfin 2.
3. If found in Jellyfin 2, offer a download option.
4. Download the media through the Jellyfin API.
5. Save it into the Jellyfin 1 Movies library.
6. Preserve the original media filename.
7. Refresh Jellyfin 1.

Target directory:

```text
/media/Movies/<Movie Name>/
```

The final filename should remain the original media filename returned by Jellyfin.

## 7. Jellyfin 2 Series State

Series require season selection.

The intended workflow is:

```text
Series found in Jellyfin 2
        │
        ▼
Show seasons
        │
        ▼
User selects season
        │
        ▼
Download selected season
        │
        ▼
Save episodes to Jellyfin 1
        │
        ▼
Refresh Jellyfin 1
```

Only the selected season should be downloaded.

Target directory:

```text
/media/Series/<Series Name>/Season XX/
```

Each episode should retain its original filename.

The entire series should not be downloaded when only one season is selected.

## 8. Jellyfin Music State

Music is currently excluded from the Jellyfin 2 download workflow.

The current scope is:

- Movies
- Series

Do not add music downloading unless explicitly requested.

## 9. Target Media Locations

Jellyfin 1 is the primary destination library.

### Movies

```text
/media/Movies/<Movie Name>/<original media filename>
```

### Series

```text
/media/Series/<Series Name>/Season XX/<original episode filename>
```

The implementation should preserve filenames rather than unnecessarily renaming media.

## 10. Important Source Files

The most relevant files for the current development work are:

```text
services/jellyfin_service.py
commands/torrent/search.py
```

### `services/jellyfin_service.py`

Responsible for Jellyfin communication and searching.

Existing functionality includes:

- Jellyfin server configuration
- Connection handling
- Media searching
- Movie/Series type handling
- Title matching
- Year matching

### `commands/torrent/search.py`

Responsible for the Discord torrent-search workflow and user interaction.

Existing functionality includes:

- Torrent search UI
- Jellyfin availability checking
- Jellyfin availability response
- Torrent-search continuation
- Existing Jackett search flow

## 11. Current Development Status

| Area | State |
|---|---|
| Discord bot | Working |
| Server management | Working |
| Monitoring | Working |
| Redfish / iDRAC | Working |
| Torrent search | Working |
| Jackett integration | Working |
| qBittorrent integration | Working |
| Jellyfin 1 search | Working |
| Jellyfin 1 availability UI | Working |
| Search for torrent after Jellyfin match | Working |
| Jellyfin 2 search workflow | In development |
| Jellyfin 2 movie download | Not completed |
| Jellyfin 2 series download | Not completed |
| Season selection | Not completed |
| Jellyfin 1 library refresh after download | Not completed |
| Jellyfin 2 music download | Out of scope |

## 12. Current Priority

The immediate priority is:

> Complete Jellyfin 2 integration.

The implementation should:

1. Keep Jellyfin 1 as the first check.
2. Only check Jellyfin 2 when Jellyfin 1 does not contain the media.
3. Show the Jellyfin 2 download option when applicable.
4. Keep the existing torrent-search option.
5. Support movie downloads.
6. Support season-based series downloads.
7. Save files into the correct Jellyfin 1 directories.
8. Preserve original filenames.
9. Refresh Jellyfin 1 after successful downloads.
10. Handle API and download failures correctly.
11. Preserve all existing torrent functionality.

## 13. Current Architecture Goal

The Jellyfin implementation should follow the existing separation between Discord UI and service/API logic.

Preferred structure:

```text
Discord command
      │
      ▼
Torrent search / UI
      │
      ▼
Jellyfin service
      │
      ▼
Jellyfin API
```

The command should handle user interaction.

The service should handle Jellyfin API communication and media operations.

## 14. Download Requirements

Jellyfin media can be large.

Downloads should therefore be streamed to disk rather than unnecessarily loading complete media files into memory.

The implementation should correctly handle:

- Connection failures
- HTTP errors
- Missing media
- Invalid API responses
- Failed downloads
- Partial downloads
- Missing destination directories
- Library refresh failures

A download must not be reported as successful unless the operation actually completed successfully.

## 15. Testing State

Jellyfin 1 availability checking has already been tested successfully.

The Jellyfin 2 download functionality still needs testing once implemented.

Required testing includes:

### Jellyfin Availability

- Movie found in Jellyfin 1
- Series found in Jellyfin 1
- Movie found in Jellyfin 2
- Series found in Jellyfin 2
- Media not found in either server

### Movie Download

- Correct movie selected
- Correct destination directory
- Original filename preserved
- Download completes
- Jellyfin 1 refresh occurs

### Series Download

- Correct series selected
- Seasons displayed
- Correct season selected
- Only selected season downloaded
- Correct episode directories used
- Original filenames preserved
- Jellyfin 1 refresh occurs

### Failure Handling

- Jellyfin unavailable
- Jellyfin API error
- Download failure
- Invalid media information
- Filesystem error

### Existing Functionality

After Jellyfin 2 changes, the normal Jackett/qBittorrent torrent workflow must still be tested.

## 16. Deployment State

Changes are deployed by rebuilding the Docker container.

Standard command:

```bash
bash scripts/rebuild.sh
```

After deployment, check the container logs and test the affected Discord command.

The production environment should not be modified with experimental changes that have not been reviewed.

## 17. Completed Work

The following work is already complete:

- Aegis-Bot source has been placed into a private Git repository.
- Sensitive configuration has been excluded from Git.
- Existing torrent functionality is preserved.
- Jellyfin service exists.
- Jellyfin 1 availability checking works.
- Jellyfin availability is integrated into torrent searching.
- Users can bypass Jellyfin availability and perform a torrent search.
- Development documentation is being maintained in the repository.

## 18. In Progress

Current work:

```text
Jellyfin 2 integration
```

Specifically:

```text
Jellyfin 2
    ├── Detect media
    ├── Movie download
    ├── Series season selection
    ├── Series season download
    ├── File placement
    ├── Library refresh
    └── Error handling
```

## 19. Not Yet Implemented

The following functionality should still be considered outstanding:

- Jellyfin 2 movie downloading
- Jellyfin 2 series downloading
- Season selection UI
- Jellyfin 2 download buttons
- Jellyfin 1 post-download library refresh
- Complete download error handling
- Full end-to-end testing of the Jellyfin 2 workflow

## 20. Scope Boundary

Do not expand the current Jellyfin task into unrelated functionality.

The immediate goal is to make this workflow reliable:

```text
User searches for media
        │
        ▼
Jellyfin 1
        │
        ├── Found → Existing behavior
        │
        └── Not found
                │
                ▼
           Jellyfin 2
                │
           ┌────┴─────┐
           │          │
        Found      Not found
           │          │
           ▼          ▼
   Download option  Torrent search
           │
           ▼
   Jellyfin 1 library
```

Once this workflow is complete and tested, further functionality can be considered separately.
