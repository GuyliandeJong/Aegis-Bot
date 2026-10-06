import json
import os
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import logger


class MonitorState:
    """
    Stores the previous state of each monitoring subsystem.

    The state is persisted to disk so that restarting the bot does not
    cause every monitor to treat the current condition as a new event.

    Successful monitoring checks are tracked separately so the bot can
    distinguish between a known component state and a stale state that
    has not been successfully verified recently.
    """

    # Store monitoring state outside the application source directory.
    # This location is backed by the persistent /app/data volume.
    STATE_FILE = Path("/app/data/monitor_state.json")

    def __init__(self):
        # Each monitor has its own state dictionary.
        self.filesystems = {}
        self.temperatures = {}
        self.storage = {}
        self.lifecycle = {}
        self.wan = {}
        self.cpu = {}
        self.memory = {}

        # Track the last successful check for each monitor.
        #
        # Example:
        # {
        #     "temperature": "2026-09-04T07:40:00+00:00",
        #     "storage": "2026-09-04T07:40:02+00:00"
        # }
        self.last_success = {}

        # Track whether a monitor has already been reported as stale.
        #
        # This prevents a persistent iDRAC failure from generating a
        # stale notification every monitoring cycle.
        self.stale = {}

        # Restore previously saved state when the bot starts.
        self.load()

    def load(self):
        """
        Load previously saved monitoring state from disk.
        """

        # There is nothing to load on the first startup.
        if not self.STATE_FILE.exists():
            return

        try:
            # Read the persisted JSON state.
            with self.STATE_FILE.open("r") as file:
                data = json.load(file)

            # Restore each monitor's state independently.
            self.filesystems = data.get("filesystems", {})
            self.temperatures = data.get("temperatures", {})
            self.storage = data.get("storage", {})
            self.lifecycle = data.get("lifecycle", {})
            self.wan = data.get("wan", {})
            self.cpu = data.get("cpu", {})
            self.memory = data.get("memory", {})

            # Restore successful-check timestamps.
            self.last_success = data.get(
                "last_success",
                {},
            )

            # Restore stale states.
            self.stale = data.get(
                "stale",
                {},
            )

            logger.info(
                "[MonitorState] Monitoring state loaded."
            )

        except Exception as ex:
            # A corrupted or unreadable state file should not prevent
            # the entire bot from starting.
            logger.error(
                "[MonitorState] Failed to load state: %s",
                ex,
            )

    def mark_success(self, monitor_name: str):
        """
        Record that a monitor completed a successful check.

        The timestamp is stored as UTC ISO-8601 text so it can safely
        be persisted to JSON and restored after a restart.

        A successful check also clears the stale state for that monitor.
        """

        self.last_success[monitor_name] = (
            datetime.now(timezone.utc).isoformat()
        )

        # The monitor has successfully checked its data again, so it
        # is no longer stale.
        self.stale[monitor_name] = False

    def get_last_success(self, monitor_name: str):
        """
        Return the last successful check timestamp for a monitor.

        Returns None when the monitor has never completed successfully.
        """

        return self.last_success.get(monitor_name)

    def is_stale(self, monitor_name: str, stale_duration: int) -> bool:
        """
        Determine whether a monitor's last successful check is stale.

        A monitor that has never successfully completed a check is not
        automatically considered stale here. That case is handled
        separately by the monitor itself so a first-startup failure does
        not create a misleading stale notification.
        """

        last_success = self.get_last_success(
            monitor_name
        )

        if last_success is None:
            return False

        try:
            last_success_time = datetime.fromisoformat(
                last_success
            )

        except (TypeError, ValueError):
            logger.warning(
                "[MonitorState] Invalid last-success timestamp "
                "for monitor %s.",
                monitor_name,
            )

            return False

        age = (
            datetime.now(timezone.utc) - last_success_time
        ).total_seconds()

        return age >= stale_duration

    def is_marked_stale(self, monitor_name: str) -> bool:
        """
        Return whether a monitor has already been reported as stale.
        """

        return self.stale.get(
            monitor_name,
            False,
        )

    def mark_stale(self, monitor_name: str):
        """
        Mark a monitor as stale after a stale notification is sent.
        """

        self.stale[monitor_name] = True

    def save(self):
        """
        Persist the current monitoring state to disk atomically.
        """

        # Combine all monitor states into one JSON structure.
        data = {
            "filesystems": self.filesystems,
            "temperatures": self.temperatures,
            "storage": self.storage,
            "lifecycle": self.lifecycle,
            "wan": self.wan,
            "cpu": self.cpu,
            "memory": self.memory,
            "last_success": self.last_success,
            "stale": self.stale,
        }

        # Use a temporary file in the same directory as STATE_FILE.
        #
        # This allows os.replace() to atomically replace the existing
        # state file without risking a partially written JSON file.
        temp_file = self.STATE_FILE.with_suffix(".tmp")

        try:
            # Ensure the persistent data directory exists.
            self.STATE_FILE.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            # Write the complete state to the temporary file.
            with temp_file.open("w") as file:
                json.dump(
                    data,
                    file,
                    indent=4,
                )

                # Flush Python's file buffer.
                file.flush()

                # Make sure the operating system has written the
                # buffered data to disk before replacing the real file.
                os.fsync(file.fileno())

            # Atomically replace the existing state file.
            os.replace(
                temp_file,
                self.STATE_FILE,
            )

        except Exception as ex:
            # State persistence failure should be logged without
            # stopping the monitoring framework.
            logger.error(
                "[MonitorState] Failed to save state: %s",
                ex,
            )

            # Clean up the temporary file if it still exists.
            try:
                if temp_file.exists():
                    temp_file.unlink()

            except Exception:
                logger.exception(
                    "[MonitorState] Failed to remove temporary "
                    "state file."
                )


# Create one shared state object.
#
# Every monitor importing `state` receives this same instance.

state = MonitorState()