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
#   BACKUP_PART_SIZE        the archive is split into parts of this size
#                           (256M), so a failed upload only repeats one
#                           part instead of the whole archive
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

# Proton Drive: an upload that fails halfway leaves a draft, and every
# retry of the same file then fails with "a draft exist". Replace it.
export RCLONE_PROTONDRIVE_REPLACE_EXISTING_DRAFT=true

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
PART_SIZE="$(env_value BACKUP_PART_SIZE)"; PART_SIZE="${PART_SIZE:-256M}"
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
# One folder per backup holding the parts <NAME>.tar.gz.000, .001, ...
# Restore: cat <NAME>/<NAME>.tar.gz.* | tar -xzf - -C /
NAME="aegis-backup-$STAMP"
ARCHIVE="$LOCAL_DIR/$NAME"
# rclone's messages from the last run, kept for troubleshooting.
RCLONE_LOG="$LOCAL_DIR/rclone.log"
STOPPED=()
SYNCS=()
CHANGED=0
UPLOADED=false

# Set once the archive is complete; a later failure (the upload) then
# keeps it locally instead of deleting it.
ARCHIVE_DONE=false

rclone_error() {
    # The first error rclone logged (the cause; later ones are usually
    # retries), without the date, as plain text for the webhook.
    local line
    line="$(grep -m 1 -E 'ERROR|CRITICAL|Failed' "$RCLONE_LOG" 2>/dev/null)"
    [ -n "$line" ] || line="$(tail -n 1 "$RCLONE_LOG" 2>/dev/null)"
    printf '%s' "$line" | sed -E 's/^[0-9]{4}\/[0-9]{2}\/[0-9]{2} [0-9:]{8} //' \
        | head -c 200 | tr '\n"\\' '   '
}

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
        size="$(du -sb "$ARCHIVE" | cut -f1)"
    else
        rm -rf "$ARCHIVE"
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

# One run at a time: a second run (cron while a manual one is still
# uploading) would stop the containers again and upload alongside it.
# It leaves the status file alone.
exec 9> "$LOCAL_DIR/.lock"
if ! flock -n 9; then
    send "Backup skipped: another backup is still running."
    echo "Backup skipped: another backup is still running." >&2
    exit 1
fi

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
# Split without expanding wildcards against the current folder.
read -ra EXTRA <<< "$EXTRA_EXCLUDE"

# A full path that does not exist excludes nothing; tar would not say
# so and the folder would silently end up in the archive.
for pattern in "${EXTRA[@]}"; do
    case "$pattern" in
        /*[*?[]*) ;;
        /*) [ -e "$pattern" ] || fail "BACKUP_EXCLUDE: $pattern does not exist" ;;
    esac
done

for pattern in "${EXCLUDES[@]}" "${EXTRA[@]}"; do
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

mkdir -p "$ARCHIVE"
tar -czf - -C / "${EXCLUDE_ARGS[@]}" "${SOURCES[@]}" 2> "$ARCHIVE.errors" \
    | split -b "$PART_SIZE" -d -a 3 - "$ARCHIVE/$NAME.tar.gz."
result=("${PIPESTATUS[@]}")

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
if [ "${result[0]}" -gt 1 ]; then
    reason="$(head -c 150 "$ARCHIVE.errors" | tr '\n"\\' '   ')"
    rm -f "$ARCHIVE.errors"
    fail "tar failed: $reason"
fi
rm -f "$ARCHIVE.errors"
[ "${result[1]}" -eq 0 ] || fail "splitting the archive failed (disk full?)"

ARCHIVE_DONE=true
SIZE="$(du -sb "$ARCHIVE" | cut -f1)"

[ -z "$MIRROR_ERROR" ] || fail "mirror of $MIRROR_ERROR"

# Keep the newest archives locally (also single-file ones from older
# versions of this script).
ls -1dt "$LOCAL_DIR"/aegis-backup-* 2>/dev/null \
    | tail -n +"$((KEEP_LOCAL + 1))" | xargs -r rm -rf

if [ -n "$REMOTE" ]; then
    command -v rclone > /dev/null || fail "rclone is not installed"

    # A retry skips the parts that already arrived (same checksum).
    rclone copy "$ARCHIVE" "$REMOTE/$NAME" --checksum --retries 10 --low-level-retries 10 \
        2> "$RCLONE_LOG" \
        || fail "upload to $REMOTE failed: $(rclone_error) (full log: $RCLONE_LOG)"
    UPLOADED=true

    # Keep the newest archives in the destination; names sort by date.
    rclone lsf "$REMOTE" 2>/dev/null \
        | grep '^aegis-backup-' | sort \
        | head -n -"$KEEP_REMOTE" \
        | while read -r old; do
            case "$old" in
                */) rclone purge "$REMOTE/${old%/}" > /dev/null 2>&1 || true ;;
                *) rclone deletefile "$REMOTE/$old" > /dev/null 2>&1 || true ;;
            esac
        done

    # Mirrors: only changed files are uploaded (compared by checksum;
    # Proton Drive cannot store modification times). The versions they
    # replace go to previous/<this run>. From here on CHANGED counts the
    # files uploaded, not the changes in the local copy.
    CHANGED=0
    for rel in "${SYNCS[@]}"; do
        rclone sync "$MIRROR_DIR/$rel" "$REMOTE/mirror/current/$rel" --links --checksum \
            --backup-dir "$REMOTE/mirror/previous/$STAMP/$rel" \
            --retries 10 --low-level-retries 10 -v 2> "$LOCAL_DIR/rclone.part"
        result=$?
        cat "$LOCAL_DIR/rclone.part" >> "$RCLONE_LOG"
        CHANGED=$((CHANGED + $(grep -c ': Copied' "$LOCAL_DIR/rclone.part" || true)))
        rm -f "$LOCAL_DIR/rclone.part"
        [ "$result" -eq 0 ] \
            || fail "mirror upload of /$rel failed: $(rclone_error) (full log: $RCLONE_LOG)"
    done

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
    if [ "$UPLOADED" = true ]; then
        mirrored=" Mirrored ${#SYNCS[@]} folder(s), $CHANGED file(s) uploaded."
    else
        mirrored=" Mirrored ${#SYNCS[@]} folder(s), $CHANGED file(s) changed."
    fi
fi

if [ "$UPLOADED" = true ]; then
    send "Backup done: $human, uploaded to $REMOTE.$mirrored"
else
    send "Backup done: $human, kept locally only.$mirrored"
fi

echo "Backup done: $ARCHIVE ($human)"
exit 0
