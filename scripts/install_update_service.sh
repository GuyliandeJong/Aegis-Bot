#!/bin/bash
# ------------------------------------------------------------
# Installs the host side of /bot update: a systemd path unit that
# starts scripts/update_host.sh the moment the bot writes
# data/update_request. systemd watches the file with inotify, so
# nothing polls and the update starts at once.
#
#   bash scripts/install_update_service.sh             install and start
#   bash scripts/install_update_service.sh --print     show the units only
#   bash scripts/install_update_service.sh --uninstall remove them
#
# Needs root once (it asks for your sudo password, here and not later).
# The update itself runs as your own user, who must be allowed to run
# docker and to fetch from the project's git remote.
# ------------------------------------------------------------

set -eu

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER="${SUDO_USER:-$(id -un)}"
UNITS="/etc/systemd/system"
NAMES="aegis-bot-update.path aegis-bot-update.service"

case "$PROJECT" in
    *" "*|*"|"*|*"&"*)
        echo "The project path must not contain spaces, | or &: $PROJECT" >&2
        exit 1
        ;;
esac

render() {
    sed -e "s|__PROJECT__|$PROJECT|g" -e "s|__USER__|$OWNER|g" \
        "$PROJECT/scripts/systemd/$1"
}

case "${1:-}" in
    --print)
        for name in $NAMES; do
            echo "# $name"
            render "$name"
            echo
        done
        exit 0
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now aegis-bot-update.path 2>/dev/null || true
    for name in $NAMES; do rm -f "$UNITS/$name"; done
    systemctl daemon-reload
    echo "Removed the Aegis-Bot update units."
    exit 0
fi

for name in $NAMES; do
    render "$name" > "$UNITS/$name"
done

systemctl daemon-reload
systemctl enable --now aegis-bot-update.path

echo "Installed. The update runs as $OWNER when the bot writes"
echo "$PROJECT/data/update_request."

if ! id -nG "$OWNER" | tr ' ' '\n' | grep -qx docker; then
    echo "WARNING: $OWNER is not in the docker group; the restart would fail." >&2
fi

echo
echo "Next: set REMOTE_UPDATE=true in $PROJECT/.env and restart the bot,"
echo "and set WATCHDOG_WEBHOOK_URL to get the result in Discord. With"
echo "DockSTARTer also set UPDATE_COMPOSE_DIR to its compose folder."
