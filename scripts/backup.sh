#!/bin/bash
# ------------------------------------------------------------
# Aegis-Bot backup
#
# Runs on the server from root's cron, outside the bot. Packs the
# configured folders into one archive, keeps the last few locally,
# uploads the newest to Proton Drive with rclone, and reports the
# result to Discord and to the weekly summary.
#
# Settings, in .env next to this project:
#   BACKUP_PATHS            folders to back up, separated by spaces
#                           (required; this project is always included)
#   BACKUP_EXCLUDE          extra patterns or folders to skip in the
#                           archive, separated by spaces
#   BACKUP_SYNC_PATHS       folders mirrored instead of packed, separated
#                           by spaces (e.g. game worlds): the first run
#                           uploads everything, later runs only the files
#                           that changed. Replaced and deleted files are
#                           kept per run under <remote>/mirror/previous.
#   BACKUP_MIRROR_DIR       local copy of those folders
#                           (<BACKUP_LOCAL_DIR>/mirror)
#   BACKUP_STOP_CONTAINERS  containers stopped while packing, separated
#                           by spaces; only those running are restarted,
#                           in reverse order (list an app before its
#                           database)
#   BACKUP_LOCAL_DIR        where archives are kept (/var/backups/aegis-bot)
#   BACKUP_KEEP_LOCAL       archives kept locally (4)
#   BACKUP_REMOTE           rclone destination, e.g. proton:Backups/home
#                           (empty: no upload)
#   BACKUP_KEEP_REMOTE      archives, and runs of replaced mirror files,
#                           kept in the destination (8)
#   WATCHDOG_WEBHOOK_URL    Discord webhook for the result (optional)
#
# Cron, as root (sudo crontab -e), Sunday 04:00:
#   0 4 * * 0 bash /path/to/Aegis-Bot/scripts/backup.sh
#
# Archives contain .env files with passwords: they are written with
# permissions for root only.
# ------------------------------------------------------------

set -u
umask 077

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS="$PROJECT/data/backup_status.json"

env_value() {
    # One value from .env; quotes and Windows line ends removed.
    grep -E "^$1=" "$PROJECT/.env" 2>/dev/null | tail -n 1 \
        | cut -d= -f2- | tr -d '"'"'"'\r'
}

PATHS="$(env_value BACKUP_PATHS)"
EXTRA_EXCLUDE="$(env_value BACKUP_EXCLUDE)"
STOP="$(env_value BACKUP_STOP_CONTAINERS)"
LOCAL_DIR="$(env_value BACKUP_LOCAL_DIR)"; LOCAL_DIR="${LOCAL_DIR:-/var/backups/aegis-bot}"
KEEP_LOCAL="$(env_value BACKUP_KEEP_LOCAL)"; KEEP_LOCAL="${KEEP_LOCAL:-4}"
REMOTE="$(env_value BACKUP_REMOTE)"
KEEP_REMOTE="$(env_value BACKUP_KEEP_REMOTE)"; KEEP_REMOTE="${KEEP_REMOTE:-8}"
WEBHOOK="$(env_value WATCHDOG_WEBHOOK_URL)"
SYNC_PATHS="$(env_value BACKUP_SYNC_PATHS)"
MIRROR_DIR="$(env_value BACKUP_MIRROR_DIR)"; MIRROR_DIR="${MIRROR_DIR:-$LOCAL_DIR/mirror}"
MIRROR_DIR="${MIRROR_DIR%/}"

# Large or throwaway content that is rebuilt by the apps themselves.
EXCLUDES=(
    "*/cache/*" "*/Cache/*" "*/.cache/*" "*/transcodes/*" "*/Transcodes/*"
    "*/logs/*" "*/Logs/*" "*/log/*" "*.log" "*.log.*" "*/crash-reports/*"
    "*/MediaCover/*" "*/metadata/*" "*/Metadata/*"
    "*/Backups/*" "*/backups/*" "*/__pycache__/*" "*.part"
    # Plex: generated thumbnails and crash dumps.
    "*/Plex Media Server/Media/*" "*/Crash Reports/*"
)

# The same for mirrored folders, in rclone's filter syntax: a pattern
# without a leading / matches at any depth.
MIRROR_EXCLUDES=(
    "cache/**" "Cache/**" ".cache/**" "logs/**" "Logs/**" "log/**"
    "*.log" "*.log.*" "crash-reports/**" "backups/**" "Backups/**"
    "__pycache__/**" "*.part"
)

STARTED="$(date +%s)"
STAMP="$(date +%Y-%m-%d_%H%M)"
NAME="aegis-backup-$STAMP.tar.gz"
ARCHIVE="$LOCAL_DIR/$NAME"
STOPPED=()
SYNCS=()
CHANGED=0
UPLOADED=false

# Set once the archive is complete; a later failure (the upload) then
# keeps it locally instead of deleting it.
ARCHIVE_DONE=false

send() {
    [ -n "$WEBHOOK" ] || return 0
    # $1 is plain text without quotes or backslashes.
    curl -sS -m 15 -H "Content-Type: application/json" \
        -d "{\"content\": \"$1\"}" "$WEBHOOK" > /dev/null || true
}

write_status() {
    # ok, size in bytes, uploaded, error text
    mkdir -p "$(dirname "$STATUS")"
    printf '{"time": %s, "ok": %s, "archive": "%s", "size": %s, "uploaded": %s, "remote": "%s", "mirrored": %s, "changed": %s, "error": "%s"}\n' \
        "$STARTED" "$1" "$NAME" "$2" "$3" "$REMOTE" "${#SYNCS[@]}" "$CHANGED" "$4" > "$STATUS"
    chmod 644 "$STATUS"
}

start_stopped() {
    # Reverse order: list an app before its database in
    # BACKUP_STOP_CONTAINERS, and the database is started first.
    local index

    for (( index=${#STOPPED[@]}-1; index>=0; index-- )); do
        docker start "${STOPPED[$index]}" > /dev/null 2>&1 \
            || send "Backup: could not start ${STOPPED[$index]} again. Start it by hand."
    done
    STOPPED=()
}

fail() {
    start_stopped
    size=0

    if [ "$ARCHIVE_DONE" = true ]; then
        size="$(stat -c %s "$ARCHIVE")"
    else
        rm -f "$ARCHIVE"
    fi

    write_status false "$size" "$UPLOADED" "$1"
    send "Backup failed: $1"
    echo "Backup failed: $1" >&2
    exit 1
}

# Containers stopped by this script are started again however it ends.
trap start_stopped EXIT

if [ -z "$PATHS" ]; then
    fail "BACKUP_PATHS is not set in .env"
fi

mkdir -p "$LOCAL_DIR" || fail "cannot create $LOCAL_DIR"
chmod 700 "$LOCAL_DIR"

# Everything to pack, as paths relative to /, so a restore can be
# unpacked with: tar -xzf <archive> -C /
SOURCES=()
PROJECT_COVERED=false

for path in $PATHS; do
    path="${path%/}"
    [ -e "$path" ] || fail "$path does not exist"
    SOURCES+=("${path#/}")

    # A listed folder that contains this project already includes it.
    case "$PROJECT/" in
        "$path"/*) PROJECT_COVERED=true ;;
    esac
done

if [ "$PROJECT_COVERED" = false ]; then
    SOURCES+=("${PROJECT#/}")
fi

EXCLUDE_ARGS=()
for pattern in "${EXCLUDES[@]}" $EXTRA_EXCLUDE; do
    # Archive entries have no leading /, so a full path from .env
    # only matches without it.
    EXCLUDE_ARGS+=("--exclude=${pattern#/}")
done

# The local backup folders may sit inside a backed-up folder.
EXCLUDE_ARGS+=("--exclude=${LOCAL_DIR#/}" "--exclude=${MIRROR_DIR#/}")

# Mirrored folders are left out of the archive.
for path in $SYNC_PATHS; do
    path="${path%/}"
    [ -e "$path" ] || fail "$path does not exist"
    SYNCS+=("${path#/}")
    EXCLUDE_ARGS+=("--exclude=${path#/}")
done

if [ "${#SYNCS[@]}" -gt 0 ]; then
    command -v rclone > /dev/null || fail "rclone is not installed"
fi

for container in $STOP; do
    if [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null)" = "true" ]; then
        docker stop "$container" > /dev/null 2>&1 || fail "could not stop $container"
        STOPPED+=("$container")
    fi
done

tar -czf "$ARCHIVE" -C / "${EXCLUDE_ARGS[@]}" "${SOURCES[@]}" 2> "$ARCHIVE.errors"
result=$?

# Copy the mirrored folders while their containers are still stopped;
# after the first run only changed files are copied.
MIRROR_ERROR=""
for rel in "${SYNCS[@]}"; do
    mkdir -p "$MIRROR_DIR/$rel"
    output="$(rclone sync "/$rel" "$MIRROR_DIR/$rel" --links --delete-excluded \
        "${MIRROR_EXCLUDES[@]/#/--exclude=}" -v 2>&1)" \
        || { MIRROR_ERROR="/$rel: $(tail -c 150 <<< "$output" | tr '\n"\\' '   ')"; break; }
    CHANGED=$((CHANGED + $(grep -cE ': (Copied|Deleted)' <<< "$output" || true)))
done

start_stopped

# tar exits 1 when a file changed while it was read; the archive is
# still complete. Anything else is a real failure.
if [ "$result" -gt 1 ]; then
    reason="$(head -c 150 "$ARCHIVE.errors" | tr '\n"\\' '   ')"
    rm -f "$ARCHIVE.errors"
    fail "tar failed: $reason"
fi
rm -f "$ARCHIVE.errors"

ARCHIVE_DONE=true
SIZE="$(stat -c %s "$ARCHIVE")"

[ -z "$MIRROR_ERROR" ] || fail "mirror of $MIRROR_ERROR"

# Keep the newest archives locally.
ls -1t "$LOCAL_DIR"/aegis-backup-*.tar.gz 2>/dev/null \
    | tail -n +"$((KEEP_LOCAL + 1))" | xargs -r rm -f

if [ -n "$REMOTE" ]; then
    command -v rclone > /dev/null || fail "rclone is not installed"

    rclone copy "$ARCHIVE" "$REMOTE" --retries 3 --low-level-retries 10 \
        2> "$LOCAL_DIR/rclone.errors" \
        || fail "upload to $REMOTE failed: $(tail -c 150 "$LOCAL_DIR/rclone.errors" | tr '\n"\\' '   ')"
    rm -f "$LOCAL_DIR/rclone.errors"
    UPLOADED=true

    # Keep the newest archives in the destination; names sort by date.
    rclone lsf "$REMOTE" --files-only 2>/dev/null \
        | grep '^aegis-backup-.*\.tar\.gz$' | sort \
        | head -n -"$KEEP_REMOTE" \
        | while read -r old; do
            rclone deletefile "$REMOTE/$old" > /dev/null 2>&1 || true
        done

    # Mirrors: only changed files are uploaded (compared by checksum;
    # Proton Drive cannot store modification times). The versions they
    # replace go to previous/<this run>.
    for rel in "${SYNCS[@]}"; do
        rclone sync "$MIRROR_DIR/$rel" "$REMOTE/mirror/current/$rel" --links --checksum \
            --backup-dir "$REMOTE/mirror/previous/$STAMP/$rel" \
            --retries 3 --low-level-retries 10 2> "$LOCAL_DIR/rclone.errors" \
            || fail "mirror upload of /$rel failed: $(tail -c 150 "$LOCAL_DIR/rclone.errors" | tr '\n"\\' '   ')"
    done
    rm -f "$LOCAL_DIR/rclone.errors"

    rclone lsf "$REMOTE/mirror/previous" --dirs-only 2>/dev/null \
        | sort | head -n -"$KEEP_REMOTE" \
        | while read -r old; do
            rclone purge "$REMOTE/mirror/previous/${old%/}" > /dev/null 2>&1 || true
        done
fi

write_status true "$SIZE" "$UPLOADED" ""

human="$(numfmt --to=iec-i --suffix=B "$SIZE" 2>/dev/null || echo "$SIZE bytes")"
mirrored=""
if [ "${#SYNCS[@]}" -gt 0 ]; then
    mirrored=" Mirrored ${#SYNCS[@]} folder(s), $CHANGED file(s) changed."
fi

if [ "$UPLOADED" = true ]; then
    send "Backup done: $human, uploaded to $REMOTE.$mirrored"
else
    send "Backup done: $human, kept locally only.$mirrored"
fi

echo "Backup done: $ARCHIVE ($human)"
exit 0
