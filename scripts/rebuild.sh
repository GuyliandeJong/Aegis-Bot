#!/bin/bash
set -e

# ------------------------------------------------------------
# Aegis-Bot restart script
#
# Recreates the aegis-bot container and shows its startup log.
#
# With DockSTARTer (the "ds" command) the container is managed by
# DockSTARTer; otherwise docker compose is used in the folder this is
# run from (the one with docker-compose.yml).
#
# When the project folder is bind-mounted into /app, source changes
# only need this restart, not an image rebuild.
# ------------------------------------------------------------

echo "Stopping Aegis-Bot..."

# Stop and remove the existing container.
# The Docker image is intentionally kept.
if command -v ds >/dev/null 2>&1; then
    ds -y -c down aegis-bot
else
    docker compose rm --stop --force aegis-bot
fi


echo
echo "Starting Aegis-Bot..."

# Record the time immediately before starting the container.
START_TIME=$(date +%s)


# Start the container using the existing image.
#
# DockSTARTer manages the foreground/background behavior,
# so do not pass Docker Compose's "-d" option to it.
if command -v ds >/dev/null 2>&1; then
    ds -y -c up aegis-bot
else
    docker compose up -d aegis-bot
fi


echo
echo "Waiting for container to start..."
sleep 3


echo
echo "Container status:"
docker ps --filter name=aegis-bot


# ------------------------------------------------------------
# Capture the current time.
#
# Docker retains logs from previous container starts.
# We use this timestamp so that only logs generated during
# this restart are displayed and checked.
# ------------------------------------------------------------




echo
echo "Waiting for Aegis-Bot to become ready..."

while true; do

    # Retrieve only logs generated after this restart.
    LOGS=$(docker logs --since "${START_TIME}s" aegis-bot 2>&1)

    # The monitoring framework starts near the end of
    # application initialization.
    if echo "$LOGS" | grep -q "Monitoring framework started"; then
        break
    fi

    # Stop waiting if the container crashed.
    STATUS=$(docker inspect \
        --format '{{.State.Status}}' \
        aegis-bot)

    if [ "$STATUS" != "running" ]; then
        echo
        echo "ERROR: Aegis-Bot stopped unexpectedly."
        echo
        docker logs --tail 100 aegis-bot
        exit 1
    fi

    sleep 1
done


echo
echo "Current startup logs:"
echo "----------------------------------------"

# Display only the logs produced by the current restart.
docker logs --since "${START_TIME}s" aegis-bot 2>&1

echo "----------------------------------------"
echo
echo "✓ Aegis-Bot restart complete."