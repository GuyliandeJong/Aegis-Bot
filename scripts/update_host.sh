#!/bin/bash
# ------------------------------------------------------------
# Aegis-Bot remote update (host side)
#
# This script runs on the server, as the server's own user, when the
# bot writes data/update_request (/bot update in Discord). It does what
# `git try <branch>` does by hand: fetch, switch, fast-forward, restart
# the container, and reports through a Discord webhook.
#
# SECURITY MODEL. Treat the bot as something that can be taken over.
# Whatever the container can write must not be able to run code on the
# host, so:
#
# - This file is not run from the project folder. The installer
#   (scripts/install_update_service.sh) copies it to a root-owned
#   location, and systemd runs that copy. Updating the project does not
#   update it; run the installer again.
# - Settings come from a root-owned file (/etc/aegis-bot/update.conf),
#   parsed here and never sourced. Nothing is read from the project
#   except data/update_request.
# - The request is a regular file (no symlink, no FIFO) of at most 1 KiB,
#   read once. Every value is checked: a branch from the allowlist in
#   the settings, a recent time, an id not handled before. Nothing in it
#   is run as a command.
# - Before every update the script asks Docker which mounts the bot's
#   container has. It refuses to run if the container can write to the
#   project folder (or a folder above or inside it, except data/), can
#   reach the Docker socket through a mount of / or /run, or is
#   privileged or shares the host's process namespace. The git hooks and
#   repository settings that git would otherwise run live in the
#   project folder; the read-only mount is what keeps the container from
#   planting them. The -c options on git below are only a backstop.
# - One update per cooldown period (default 5 minutes), one at a time.
#
# The restart is plain `docker compose` as your own user: no sudo. It
# does not rebuild the image or regenerate DockSTARTer's compose file.
#
# Test hooks (never set by the systemd unit): AEGIS_UPDATE_CONF,
# AEGIS_UPDATE_TESTING (skips the root-ownership check of the settings),
# AEGIS_UPDATE_SOCKET.
#
#   update_host.sh                 handle a request, if there is one
#   update_host.sh --check-mounts  only check the container's mounts
# ------------------------------------------------------------

set -u

CONF="${AEGIS_UPDATE_CONF:-/etc/aegis-bot/update.conf}"
TESTING="${AEGIS_UPDATE_TESTING:-}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/aegis-bot"
LOG="$STATE_DIR/update.log"
LAST="$STATE_DIR/update_last_id"
LAST_TIME="$STATE_DIR/update_last_time"
READY_TIMEOUT="${UPDATE_READY_TIMEOUT:-180}"
MAX_AGE="${UPDATE_MAX_AGE:-600}"

die() { echo "$*" >&2; exit 1; }

mkdir -p "$STATE_DIR" 2>/dev/null || die "Cannot create $STATE_DIR"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

# ---- settings: a root-owned file, read as plain key=value lines --------

[ -f "$CONF" ] && [ ! -L "$CONF" ] || die "No settings file $CONF (run scripts/install_update_service.sh)"

if [ -z "$TESTING" ]; then
    [ "$(stat -c %u "$CONF")" = 0 ] || die "$CONF is not owned by root"
    [ $(( 8#$(stat -c %a "$CONF") & 8#022 )) -eq 0 ] || die "$CONF is writable by others"
fi

conf() { sed -n "s/^$1=//p" "$CONF" | head -n 1 | tr -d '\r'; }

PROJECT="$(conf project)"
OWNER="$(conf owner)"
COMPOSE_DIR="$(conf compose_dir)"
WEBHOOK="$(conf webhook)"
BRANCHES="$(conf branches)"
COOLDOWN="$(conf cooldown)"
CONTAINER="$(conf container)"
CONTAINER="${CONTAINER:-aegis-bot}"
COOLDOWN="${COOLDOWN:-300}"
BRANCHES="${BRANCHES:-main}"

PATH_RE='^/[A-Za-z0-9._/-]{1,200}$'
[[ "$PROJECT" =~ $PATH_RE ]] && [[ "$PROJECT" != *..* ]] || die "Bad project path in $CONF"
[[ "$OWNER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "Bad owner in $CONF"
[ -z "$COMPOSE_DIR" ] || { [[ "$COMPOSE_DIR" =~ $PATH_RE ]] && [[ "$COMPOSE_DIR" != *..* ]]; } || die "Bad compose_dir in $CONF"
[ -z "$WEBHOOK" ] || [[ "$WEBHOOK" =~ ^https://[A-Za-z0-9._~:/?#@!\$\&\(\)*+,\;=%-]{1,300}$ ]] || die "Bad webhook in $CONF"
[[ "$BRANCHES" =~ ^[A-Za-z0-9][A-Za-z0-9._/,-]{0,300}$ ]] || die "Bad branches in $CONF"
[[ "$COOLDOWN" =~ ^[0-9]{1,5}$ ]] || die "Bad cooldown in $CONF"
[[ "$CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$ ]] || die "Bad container in $CONF"

[ -z "$TESTING" ] && { [ "$(id -un)" = "$OWNER" ] || die "Runs as $(id -un), the settings say $OWNER"; }

PROJECT="$(realpath -m "$PROJECT")"

send() {
    # Plain text for a Discord webhook: no quotes, backslashes or newlines.
    [ -n "$WEBHOOK" ] || return 0
    local text
    text="$(printf '%s' "$1" | tr -d '"\\' | tr '\n\r' '  ' | cut -c1-1800)"
    curl -sS -m 15 -H "Content-Type: application/json" \
        -d "{\"content\": \"$text\"}" "$WEBHOOK" > /dev/null 2>&1
}

# ---- what the container can reach ------------------------------------

check_mounts() {
    # Prints one line per problem and returns 1 when there is any.
    local out head line type rw src real sock found=0
    local sockets=()

    out="$(docker inspect "$CONTAINER" --format '{{.HostConfig.Privileged}} {{.HostConfig.PidMode}}{{"\n"}}{{range .Mounts}}{{.Type}} {{.RW}} {{.Source}}{{"\n"}}{{end}}' 2>/dev/null)" \
        || { echo "cannot inspect the container $CONTAINER"; return 1; }

    for sock in "${AEGIS_UPDATE_SOCKET:-/var/run/docker.sock}" /run/docker.sock; do
        sockets+=("$(realpath -m "$sock")")
    done

    head="$(printf '%s\n' "$out" | head -n 1)"

    [[ "$head" == true* ]] && { echo "the container is privileged"; found=1; }
    [[ "$head" == *" host" ]] && { echo "the container shares the host's process namespace"; found=1; }

    while read -r type rw src; do
        [ "$type" = bind ] || continue
        real="$(realpath -m "$src")"

        for sock in "${sockets[@]}"; do
            if [ "$real" = / ] || [ "$sock" = "$real" ] || [[ "$sock" == "$real"/* ]]; then
                echo "a mount of $real reaches the Docker socket"
                found=1
                break
            fi
        done

        [ "$rw" = true ] || continue

        if [ "$real" = "$PROJECT" ] || [ "$real" = / ] || [[ "$PROJECT" == "$real"/* ]]; then
            echo "the container can write to $real, which contains the project folder"
            found=1
        elif [[ "$real" == "$PROJECT"/* ]] && [ "$real" != "$PROJECT/data" ] && [[ "$real" != "$PROJECT/data/"* ]]; then
            echo "the container can write to $real inside the project folder"
            found=1
        fi
    done < <(printf '%s\n' "$out" | tail -n +2)

    return "$found"
}

if [ "${1:-}" = "--check-mounts" ]; then
    if reasons="$(check_mounts)"; then
        echo "The container's mounts are safe for remote updates."
        exit 0
    fi

    printf '%s\n' "$reasons" >&2
    exit 1
fi

# ---- the request ------------------------------------------------------

REQUEST="$PROJECT/data/update_request"

# A regular file only: a symlink or a FIFO is ignored without being opened.
{ [ -f "$REQUEST" ] && [ ! -L "$REQUEST" ]; } || exit 0
[ "$(realpath "$REQUEST")" = "$PROJECT/data/update_request" ] || exit 0
size="$(stat -c %s "$REQUEST" 2>/dev/null || echo 0)"
{ [ "$size" -gt 0 ] && [ "$size" -le 1024 ]; } || { log "ignored: request size $size"; exit 0; }

# One update at a time; a request that arrives meanwhile is dropped.
exec 9>"$STATE_DIR/update.lock"
flock -n 9 || exit 0

content="$(head -c 1024 "$REQUEST")"
field() { printf '%s\n' "$content" | sed -n "s/^$1=//p" | head -n 1 | tr -d '\r'; }

id="$(field id)"
branch="$(field branch)"
stamp="$(field time)"
by="$(field by)"
now="$(date +%s)"

# A request that is damaged, old or already handled is ignored quietly.
[[ "$id" =~ ^[A-Za-z0-9-]{8,64}$ ]] || { log "ignored: bad id"; exit 0; }
[[ "$stamp" =~ ^[0-9]{9,12}$ ]] || { log "ignored $id: bad time"; exit 0; }

if [ "$(( now - stamp ))" -gt "$MAX_AGE" ] || [ "$(( stamp - now ))" -gt 60 ]; then
    log "ignored $id: not recent"
    exit 0
fi

[ "$(cat "$LAST" 2>/dev/null)" = "$id" ] && exit 0

# Remembered before anything runs, so a failed update is not repeated.
echo "$id" > "$LAST"

mention=""
[[ "$by" =~ ^[0-9]{1,25}$ ]] && mention=" (requested by <@$by>)"

refuse() {
    log "refused $id: $1"
    send "Update refused$mention: $1."
    exit 0
}

if ! [[ "$branch" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$ ]] \
    || [[ "$branch" == *..* ]] || [[ "$branch" == */ ]] \
    || [[ "$branch" == *.lock ]] || [[ "$branch" == *//* ]] \
    || [[ "$branch" == *. ]]; then
    refuse "that is not a valid branch name"
fi

case ",$BRANCHES," in
    *",$branch,"*) ;;
    *) refuse "$branch is not in the branches this server allows ($BRANCHES)" ;;
esac

last_time="$(cat "$LAST_TIME" 2>/dev/null || echo 0)"
[[ "$last_time" =~ ^[0-9]+$ ]] || last_time=0

if [ "$(( now - last_time ))" -lt "$COOLDOWN" ]; then
    refuse "another update started less than $(( COOLDOWN / 60 )) minutes ago"
fi

if ! problems="$(check_mounts)"; then
    refuse "the bot's container is not isolated enough: $(printf '%s' "$problems" | tr '\n' ';'). See the docs on remote updates"
fi

echo "$now" > "$LAST_TIME"

fail() {
    log "FAILED $id: $1"
    local tail_text
    tail_text="$(tail -n 4 "$LOG" | tr -d '"\\' | tr '\n' ' ')"
    send "Update to $branch failed$mention: $1. Last log lines: $tail_text"
    exit 0
}

log "update $id to $branch$mention"
send "Updating Aegis-Bot to $branch$mention. The bot restarts in a moment."

cd "$PROJECT" || fail "the project folder is missing"

# The mount check above is what keeps the container from planting hooks
# or settings in the repository; these only add a backstop.
git() { command git -c core.hooksPath=/dev/null -c core.fsmonitor=false "$@"; }

before="$(git rev-parse --short HEAD 2>/dev/null)"

git fetch --prune origin >> "$LOG" 2>&1 || fail "git fetch failed"

git ls-remote --exit-code --heads origin "$branch" >> "$LOG" 2>&1 \
    || fail "there is no branch $branch on origin"

if git show-ref --verify --quiet "refs/heads/$branch"; then
    git switch "$branch" >> "$LOG" 2>&1 \
        || fail "git switch failed (uncommitted changes in the project folder?)"
else
    git switch -c "$branch" --track "origin/$branch" >> "$LOG" 2>&1 \
        || fail "git switch failed"
fi

git pull --ff-only origin "$branch" >> "$LOG" 2>&1 \
    || fail "$branch cannot be fast-forwarded"

after="$(git rev-parse --short HEAD 2>/dev/null)"

started="$(date +%s)"

if [ -n "$COMPOSE_DIR" ]; then
    docker compose --project-directory "$COMPOSE_DIR" down --remove-orphans "$CONTAINER" >> "$LOG" 2>&1 \
        || fail "docker compose down failed"
    docker compose --project-directory "$COMPOSE_DIR" up -d --remove-orphans --pull missing "$CONTAINER" >> "$LOG" 2>&1 \
        || fail "docker compose up failed"
else
    docker compose rm --stop --force "$CONTAINER" >> "$LOG" 2>&1 \
        || fail "docker compose rm failed"
    docker compose up -d "$CONTAINER" >> "$LOG" 2>&1 \
        || fail "docker compose up failed"
fi

# The bot logs this near the end of its start-up, like rebuild.sh waits for.
waited=0

while [ "$waited" -lt "$READY_TIMEOUT" ]; do
    if docker logs "$CONTAINER" 2>&1 | grep -q "Monitoring framework started"; then
        took=$(( $(date +%s) - started ))
        log "done $id: $before -> $after in ${took}s"

        if [ "$before" = "$after" ]; then
            send "Aegis-Bot restarted on $branch ($after, already up to date), ready after ${took}s."
        else
            send "Aegis-Bot updated to $branch: $before -> $after, ready after ${took}s."
        fi

        exit 0
    fi

    if [ "$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null)" != "running" ]; then
        docker logs --tail 20 "$CONTAINER" >> "$LOG" 2>&1
        fail "the container stopped after starting; see docker logs $CONTAINER"
    fi

    sleep 2
    waited=$(( waited + 2 ))
done

docker logs --tail 20 "$CONTAINER" >> "$LOG" 2>&1
fail "the bot did not finish starting within ${READY_TIMEOUT}s"
