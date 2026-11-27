#!/bin/bash
# ------------------------------------------------------------
# Installs the host side of /bot update: a systemd path unit that starts
# the update script the moment the bot writes data/update_request.
# systemd watches the file with inotify, so nothing polls.
#
#   bash scripts/install_update_service.sh [options]      install
#   bash scripts/install_update_service.sh --print        show the units
#   bash scripts/install_update_service.sh --uninstall    remove them
#
# Options:
#   --compose-dir DIR    DockSTARTer's compose folder (docker compose is
#                        run with --project-directory DIR); default: the
#                        project folder
#   --webhook URL        Discord webhook for the start and the result
#   --branches a,b       branches /bot update may switch to (default: main)
#   --cooldown SECONDS   minimum time between two updates (default: 300)
#
# Run it as your own user; it asks for your sudo password once. It
# copies scripts/update_host.sh to /usr/local/lib/aegis-bot/ and writes
# /etc/aegis-bot/update.conf, both root-owned, so nothing the bot's
# container can write decides what runs. It refuses to switch the path
# unit on while the container can write to the project folder, can
# reach the Docker socket, or is privileged (see docs/features.md).
# Run it again after updating the project to install a newer script.
#
# Review the project folder first: this copies code from it as root.
#   git status; git diff origin/main --stat; ls .git/hooks;
#   git config --local --list
#
# Test hooks (never set by the units): AEGIS_UPDATE_ROOT puts every
# file under that folder, AEGIS_UPDATE_TESTING skips sudo, ownership and
# systemctl.
# ------------------------------------------------------------

set -eu

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${AEGIS_UPDATE_ROOT:-}"
TESTING="${AEGIS_UPDATE_TESTING:-}"
SCRIPT_REAL="/usr/local/lib/aegis-bot/update_host.sh"
SCRIPT_DIR="$ROOT/usr/local/lib/aegis-bot"
CONF_DIR="$ROOT/etc/aegis-bot"
UNIT_DIR="$ROOT/etc/systemd/system"
NAMES="aegis-bot-update.path aegis-bot-update.service"

COMPOSE_DIR=""
WEBHOOK=""
BRANCHES="main"
COOLDOWN="300"
ACTION="install"
ARGS=("$@")

die() { echo "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --compose-dir) COMPOSE_DIR="${2:-}"; shift 2 ;;
        --webhook) WEBHOOK="${2:-}"; shift 2 ;;
        --branches) BRANCHES="${2:-}"; shift 2 ;;
        --cooldown) COOLDOWN="${2:-}"; shift 2 ;;
        --print) ACTION=print; shift ;;
        --uninstall) ACTION=uninstall; shift ;;
        *) die "Unknown option: $1" ;;
    esac
done

PATH_RE='^/[A-Za-z0-9._/-]{1,200}$'

case "$PROJECT" in
    *" "*|*"|"*|*"&"*) die "The project path must not contain spaces, | or &: $PROJECT" ;;
esac
[[ "$PROJECT" =~ $PATH_RE ]] || die "The project path has characters this script does not accept: $PROJECT"
[ -z "$COMPOSE_DIR" ] || [[ "$COMPOSE_DIR" =~ $PATH_RE ]] || die "--compose-dir must be an absolute path"
[ -z "$WEBHOOK" ] || [[ "$WEBHOOK" =~ ^https://[A-Za-z0-9._~:/?#@!\$\&\(\)*+,\;=%-]{1,300}$ ]] || die "--webhook must be an https URL"
[[ "$BRANCHES" =~ ^[A-Za-z0-9][A-Za-z0-9._/,-]{0,300}$ ]] || die "--branches must be a comma separated list of branch names"
[[ "$COOLDOWN" =~ ^[0-9]{1,5}$ ]] || die "--cooldown must be a number of seconds"

OWNER="${SUDO_USER:-$(id -un)}"

render() {
    sed -e "s|__PROJECT__|$PROJECT|g" -e "s|__USER__|$OWNER|g" \
        -e "s|__SCRIPT__|$SCRIPT_REAL|g" "$PROJECT/scripts/systemd/$1"
}

if [ "$ACTION" = print ]; then
    for name in $NAMES; do
        echo "# $name"
        render "$name"
        echo
    done
    exit 0
fi

if [ -z "$TESTING" ] && [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "${ARGS[@]}"
fi

[ "$OWNER" != root ] || die "Run this as your own user (it uses sudo itself), not as root."

if [ "$ACTION" = uninstall ]; then
    [ -n "$TESTING" ] || systemctl disable --now aegis-bot-update.path 2>/dev/null || true
    for name in $NAMES; do rm -f "$UNIT_DIR/$name"; done
    rm -f "$SCRIPT_DIR/update_host.sh" "$CONF_DIR/update.conf"
    [ -n "$TESTING" ] || systemctl daemon-reload
    echo "Removed the Aegis-Bot update units, script and settings."
    exit 0
fi

OWN_ROOT=()
OWN_CONF=()

if [ -z "$TESTING" ]; then
    OWN_ROOT=(-o root -g root)
    OWN_CONF=(-o root -g "$(id -gn "$OWNER")")
fi

install -d -m 0755 "${OWN_ROOT[@]}" "$SCRIPT_DIR" "$CONF_DIR" "$UNIT_DIR"
install -m 0755 "${OWN_ROOT[@]}" "$PROJECT/scripts/update_host.sh" "$SCRIPT_DIR/update_host.sh"

TMP_CONF="$(mktemp)"
cat > "$TMP_CONF" <<CONF
project=$PROJECT
owner=$OWNER
compose_dir=$COMPOSE_DIR
webhook=$WEBHOOK
branches=$BRANCHES
cooldown=$COOLDOWN
container=aegis-bot
CONF
install -m 0640 "${OWN_CONF[@]}" "$TMP_CONF" "$CONF_DIR/update.conf"
rm -f "$TMP_CONF"

# The installed script checks the container the same way before every
# update. Nothing is switched on while the check fails.
CHECK=("$SCRIPT_DIR/update_host.sh" --check-mounts)

if [ -n "$TESTING" ]; then
    export AEGIS_UPDATE_CONF="$CONF_DIR/update.conf"
    RUN_CHECK=("${CHECK[@]}")
else
    RUN_CHECK=(sudo -u "$OWNER" -H "${CHECK[@]}")
fi

if ! "${RUN_CHECK[@]}"; then
    echo >&2
    echo "Not switched on: the container is not isolated enough for remote updates." >&2
    echo "Fix the mounts above (docs/features.md, remote updates), then run this again." >&2
    exit 1
fi

for name in $NAMES; do
    render "$name" > "$UNIT_DIR/$name"
done

if [ -z "$TESTING" ]; then
    systemctl daemon-reload
    systemctl enable --now aegis-bot-update.path
fi

echo "Installed. The update runs as $OWNER when the bot writes"
echo "$PROJECT/data/update_request, for these branches: $BRANCHES."

if [ -z "$TESTING" ] && ! id -nG "$OWNER" | tr ' ' '\n' | grep -qx docker; then
    echo "WARNING: $OWNER is not in the docker group; the restart would fail." >&2
fi

echo
echo "Next: set REMOTE_UPDATE=true in $PROJECT/.env and restart the bot."
