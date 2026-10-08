#!/bin/bash
# ------------------------------------------------------------
# Aegis-Bot watchdog
#
# Runs on the server from cron, outside the bot. Posts to a Discord
# webhook when the bot is down, and once more when it is back.
#
# "Down" means: the aegis-bot container is not running, or the bot
# has not written data/heartbeat for HEARTBEAT_MAX_AGE seconds
# (it hangs, or has lost its Discord connection).
#
# Needs in .env (next to this project):
#   WATCHDOG_WEBHOOK_URL=https://discord.com/api/webhooks/...
#
# Cron, every five minutes, in the crontab of your own user (it must
# be allowed to run docker; no sudo needed):
#   crontab -e
#   */5 * * * * bash /path/to/Aegis-Bot/scripts/watchdog.sh
#
# Whether "down" was already reported is kept in your home folder,
# because the container owns data/ and your user cannot write there.
#
# It cannot report a server that is off or offline: nothing runs
# then to send the message.
# ------------------------------------------------------------

set -u

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="aegis-bot"
HEARTBEAT="$PROJECT/data/heartbeat"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/aegis-bot"
STATE="$STATE_DIR/watchdog_state"
HEARTBEAT_MAX_AGE="${HEARTBEAT_MAX_AGE:-600}"

# Read only this one value from .env; the rest stays untouched.
WEBHOOK="$(grep -E '^WATCHDOG_WEBHOOK_URL=' "$PROJECT/.env" 2>/dev/null \
    | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' \r')"

if [ -z "$WEBHOOK" ]; then
    echo "WATCHDOG_WEBHOOK_URL is not set in $PROJECT/.env" >&2
    exit 1
fi

problem=""

running="$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)"

if [ "$running" != "true" ]; then
    problem="the $CONTAINER container is not running"
elif [ ! -f "$HEARTBEAT" ]; then
    problem="the bot has not written a heartbeat yet"
else
    now="$(date +%s)"
    last="$(cat "$HEARTBEAT" 2>/dev/null || echo 0)"
    age=$(( now - ${last:-0} ))

    if [ "$age" -gt "$HEARTBEAT_MAX_AGE" ]; then
        problem="no heartbeat for $(( age / 60 )) minutes (hung or disconnected from Discord)"
    fi
fi

if ! mkdir -p "$STATE_DIR" 2>/dev/null || ! touch "$STATE" 2>/dev/null; then
    echo "Cannot write $STATE; the down message would repeat every run." >&2
    exit 1
fi

previous="$(cat "$STATE" 2>/dev/null)"
previous="${previous:-up}"

send() {
    # $1 is plain text without quotes or backslashes.
    curl -sS -m 15 -H "Content-Type: application/json" \
        -d "{\"content\": \"$1\"}" "$WEBHOOK" > /dev/null
}

if [ -n "$problem" ] && [ "$previous" != "down" ]; then
    send "Aegis-Bot is down: $problem. Check with: docker logs --tail 50 $CONTAINER"
    echo down > "$STATE"
elif [ -z "$problem" ] && [ "$previous" = "down" ]; then
    send "Aegis-Bot is running again."
    echo up > "$STATE"
fi

exit 0
