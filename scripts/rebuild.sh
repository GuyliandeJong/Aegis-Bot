#!/bin/bash
set -e

# ------------------------------------------------------------
# Aegis-Bot restart script
#
# Python source files are bind-mounted into /app.
# Normal source changes therefore only require restarting
# the container. The Docker image does not need rebuilding.
# ------------------------------------------------------------

echo "Stopping Aegis-Bot..."

# Stop and remove the existing container.
# The Docker image is intentionally kept.
ds -y -c down aegis-bot


echo
echo "Starting Aegis-Bot..."

# Record the time immediately before starting the container.
START_TIME=$(date +%s)


# Start the container using the existing image and the
# current source files from the /app bind mount.
#
# DockSTARTer manages the foreground/background behavior,
# so do not pass Docker Compose's "-d" option here.
ds -y -c up aegis-bot


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