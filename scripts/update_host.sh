#!/bin/bash
# ------------------------------------------------------------
# Aegis-Bot remote update (host side)
#
# Started by systemd as the server's own user the moment the bot
# writes data/update_request (/bot update in Discord), see
# scripts/install_update_service.sh. It does what `git try <branch>`
# does by hand: fetch, switch, fast-forward, restart the container. It
# reports the start and the result through the webhook of the watchdog
# (WATCHDOG_WEBHOOK_URL in .env); without it, only the log is written.
#
# The request is plain text, written by the bot:
#   id=<unique id>  branch=<branch>  time=<epoch seconds>  by=<user id>
# Every value is checked again here. A request is only run once (its id
# is remembered), only when it is recent, and only for a plain branch
# name that exists on origin. Nothing in the file is ever run as a
# command.
#
# The restart uses `docker compose` directly, as your own user, so no
# sudo and no password are needed (your user must be allowed to run
# docker). With DockSTARTer set UPDATE_COMPOSE_DIR in .env to its
# compose folder; otherwise docker compose runs in the project folder.
# This does not regenerate DockSTARTer's merged compose file, and it
# does not rebuild the image: changes to the Dockerfile or
# requirements.txt still need scripts/rebuild.sh by hand.
#
# Log and state are kept in your home folder, because the container
# owns data/:
#   ~/.local/state/aegis-bot/update.log, update_last_id
# ------------------------------------------------------------

set -u

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="aegis-bot"
REQUEST="$PROJECT/data/update_request"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/aegis-bot"
LAST="$STATE_DIR/update_last_id"
LOG="$STATE_DIR/update.log"
MAX_AGE="${UPDATE_MAX_AGE:-600}"
READY_TIMEOUT="${UPDATE_READY_TIMEOUT:-180}"

# Read only the named value from .env; the rest stays untouched.
env_value() {
    grep -E "^$1=" "$PROJECT/.env" 2>/dev/null \
        | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' \r'
}

WEBHOOK="$(env_value WATCHDOG_WEBHOOK_URL)"
COMPOSE_DIR="$(env_value UPDATE_COMPOSE_DIR)"

[ -f "$REQUEST" ] || exit 0

mkdir -p "$STATE_DIR" 2>/dev/null || { echo "Cannot create $STATE_DIR" >&2; exit 1; }

# One update at a time; a request that arrives meanwhile is dropped.
exec 9>"$STATE_DIR/update.lock"
flock -n 9 || exit 0

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

send() {
    # Plain text for a Discord webhook: no quotes, backslashes or newlines.
    [ -n "$WEBHOOK" ] || return 0
    local text
    text="$(printf '%s' "$1" | tr -d '"\\' | tr '\n\r' '  ' | cut -c1-1800)"
    curl -sS -m 15 -H "Content-Type: application/json" \
        -d "{\"content\": \"$text\"}" "$WEBHOOK" > /dev/null 2>&1
}

field() { sed -n "s/^$1=//p" "$REQUEST" | head -n 1 | tr -d '\r'; }

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

if ! [[ "$branch" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$ ]] \
    || [[ "$branch" == *..* ]] || [[ "$branch" == */ ]] \
    || [[ "$branch" == *.lock ]] || [[ "$branch" == *// ]] \
    || [[ "$branch" == *//* ]] || [[ "$branch" == *. ]]; then
    log "refused $id: bad branch name"
    send "Update refused$mention: that is not a valid branch name."
    exit 0
fi

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
