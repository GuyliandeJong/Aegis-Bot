"""Constants used by the torrent command system."""

# These are the only qBittorrent categories exposed through Aegis-Bot.
#
# qBittorrent itself remains the source of truth for the category
# configuration and the corresponding save paths.
ALLOWED_CATEGORIES = (
    "Movies",
    "Music",
    "Series",
    "PC",
)


# qBittorrent states that Aegis-Bot considers active/downloading.
ACTIVE_STATES = {
    "downloading",
    "forceddl",
    "metadl",
    "stalleddl",
    "queuedl",
    "pauseddl",
    "checkingdl",
    "checkingresumedata",
    "allocating",
    "error",
    "missingfiles",
}


# qBittorrent states representing completed torrents that are
# available for seeding.
SEEDING_STATES = {
    "uploading",
    "forcedup",
    "stalledup",
    "queueup",
    "pausedup",
    "completed",
    "checkingup",
}


# States that explicitly indicate a stopped torrent.
STOPPED_STATES = {
    "stoppeddl",
    "stoppedup",
    "stopped",
}


# States that explicitly indicate a paused torrent.
PAUSED_STATES = {
    "pauseddl",
    "pausedup",
    "paused",
}


# qBittorrent state -> human-readable label.
STATE_LABELS = {
    "downloading": "Downloading",
    "forceddl": "Downloading — Forced",
    "metadl": "Downloading — Metadata",
    "stalleddl": "Downloading — Stalled",
    "queuedl": "Queued — Downloading",
    "pauseddl": "Paused — Downloading",
    "checkingdl": "Checking — Download",
    "checkingresumedata": "Checking Resume Data",
    "allocating": "Allocating",
    "error": "Error",
    "missingfiles": "Missing Files",
    "uploading": "Seeding",
    "forcedup": "Seeding — Forced",
    "stalledup": "Seeding — Stalled",
    "queueup": "Queued — Seeding",
    "pausedup": "Paused — Seeding",
    "completed": "Completed",
    "checkingup": "Checking — Seeding",
    "stoppedup": "Stopped — Seeding",
    "stoppeddl": "Stopped — Downloading",
    "stopped": "Stopped",
    "paused": "Paused",
    "checking": "Checking",
    "moving": "Moving",
}