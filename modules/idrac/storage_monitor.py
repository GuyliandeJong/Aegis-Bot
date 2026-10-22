import os

from config.settings import settings
from core.base_monitor import BaseMonitor
from core.state import state
from modules.idrac.redfish_client import RedfishClient
from modules.idrac.storage_service import StorageService
from utils.logger import logger


class StorageMonitor(BaseMonitor):
    """
    Monitors the health of the RAID controller, virtual disks,
    and physical drives.

    Notifications are only sent when a component changes state.

    Storage health is normalized to three internal states:

        healthy
        warning
        critical

    Redfish may report values such as "OK", "Online", "Degraded",
    or "Failed". Those values are converted into the internal states
    above so the persisted monitoring state remains consistent.

    Successful storage checks are tracked separately from storage
    health. This allows Aegis-Bot to distinguish between:

        - a storage component actually becoming unhealthy
        - iDRAC/storage information becoming unavailable

    If storage information cannot be retrieved for longer than
    MONITOR_STALE_DURATION, a single stale notification is sent.

    The previously known storage health is not changed when the
    Redfish service is unavailable.
    """

    MONITOR_NAME = "storage"

    def __init__(self, notification_service):
        self.notification_service = notification_service

    async def check(self):
        """
        Perform one storage monitoring cycle.

        A new Redfish client is created for each check and is always
        closed afterwards, including when an exception occurs.
        """

        client = RedfishClient()

        try:
            service = StorageService(client)

            try:
                # Retrieve the complete storage status.
                #
                # This includes:
                # - RAID controller
                # - virtual disks
                # - physical drives
                status = await service.get_storage_status()

            except Exception as ex:
                # The storage information could not be retrieved.
                #
                # Do not change any previously known storage health.
                # Instead, determine whether monitoring itself has
                # become stale.
                logger.warning(
                    "[StorageMonitor] Unable to retrieve storage "
                    "information: %s",
                    ex,
                )

                await self._check_stale()
                return

            # The storage service successfully returned current data.
            #
            # Capture the stale state before mark_success() clears it.
            was_stale = state.is_marked_stale(
                self.MONITOR_NAME
            )

            state.mark_success(
                self.MONITOR_NAME
            )

            state.save()

            # If storage monitoring was previously stale, report that
            # current storage information is available again.
            if was_stale:
                await self.notification_service.recovered(
                    title="Storage Monitoring Recovered",
                    description=(
                        "Aegis-Bot has successfully retrieved storage "
                        "information from iDRAC again.\n\n"
                        f"**Stale Duration**\n"
                        f"`{self._format_duration(settings.MONITOR_STALE_DURATION)}`"
                    ),
                )

                logger.info(
                    "[StorageMonitor] Storage monitoring recovered."
                )

            # --------------------------------------------------------
            # RAID CONTROLLER
            # --------------------------------------------------------

            controller = status.controller

            current = self._normalize_health(
                controller.health
            )

            previous = self._normalize_previous_state(
                state.storage.get("controller")
            )

            if previous != current:
                logger.info(
                    "[StorageMonitor] Controller: %s -> %s",
                    previous or "unknown",
                    current,
                )

                # Do not notify on the first check.
                #
                # This also prevents an old persisted state such as
                # "healthy" from generating a false recovery when
                # Redfish reports "OK".
                if previous is not None:
                    await self._notify_component(
                        component=f"RAID Controller — {controller.model}",
                        current=current,
                        details=(
                            f"**Model**\n"
                            f"{controller.model}\n\n"
                            f"**Firmware**\n"
                            f"{controller.firmware}\n\n"
                            f"**Health**\n"
                            f"{controller.health}"
                        ),
                    )

                state.storage["controller"] = current
                state.save()

            # --------------------------------------------------------
            # VIRTUAL DISKS
            # --------------------------------------------------------

            volumes_state = state.storage.setdefault(
                "volumes",
                {},
            )

            for volume in status.volumes:
                volume_key = volume.name

                current = self._get_volume_status(
                    volume.health,
                    volume.raid_status,
                )

                # Older versions of the storage monitor stored a
                # dictionary here, for example:
                #
                # {
                #     "health": "healthy",
                #     "raid_status": "Online"
                # }
                #
                # Convert that old format into the new normalized
                # state before comparing it.
                previous_raw = volumes_state.get(
                    volume_key
                )

                previous = self._normalize_previous_state(
                    previous_raw
                )

                if previous != current:
                    logger.info(
                        "[StorageMonitor] Volume %s: %s -> %s",
                        volume.name,
                        previous or "unknown",
                        current,
                    )

                    # Do not notify when migrating an old state format.
                    #
                    # If the normalized old state already matches the
                    # current condition, the comparison above prevents
                    # any notification.
                    if previous is not None:
                        await self._notify_component(
                            component=f"Virtual Disk — {volume.name}",
                            current=current,
                            details=(
                                f"**Name**\n"
                                f"{volume.name}\n\n"
                                f"**RAID Type**\n"
                                f"{volume.raid_type}\n\n"
                                f"**Capacity**\n"
                                f"{volume.capacity}\n\n"
                                f"**Health**\n"
                                f"{volume.health}\n\n"
                                f"**RAID Status**\n"
                                f"{volume.raid_status}"
                            ),
                        )

                    volumes_state[volume_key] = current
                    state.save()

                elif previous_raw != current:
                    # The condition has not changed, but the persisted
                    # representation was using the old dictionary format.
                    #
                    # Normalize it silently so future monitoring cycles
                    # use the new state format.
                    volumes_state[volume_key] = current
                    state.save()

            # --------------------------------------------------------
            # PHYSICAL DRIVES
            # --------------------------------------------------------

            drives_state = state.storage.setdefault(
                "drives",
                {},
            )

            for drive in status.drives:
                drive_key = str(
                    drive.slot
                )

                current = self._get_drive_status(
                    drive.health,
                    drive.raid_status,
                    drive.predictive_failure,
                    getattr(drive, "media_life_left", None),
                )

                # Older versions stored a dictionary containing the
                # individual drive properties. Normalize that state
                # before comparing it with the current state.
                previous_raw = drives_state.get(
                    drive_key
                )

                previous = self._normalize_previous_state(
                    previous_raw
                )

                if previous != current:
                    logger.info(
                        "[StorageMonitor] Drive %s: %s -> %s",
                        drive.slot,
                        previous or "unknown",
                        current,
                    )

                    if previous is not None:
                        await self._notify_component(
                            component=f"Physical Drive — Slot {drive.slot}",
                            current=current,
                            details=(
                                f"**Slot**\n"
                                f"{drive.slot}\n\n"
                                f"**Model**\n"
                                f"{drive.model}\n\n"
                                f"**Capacity**\n"
                                f"{drive.capacity}\n\n"
                                f"**Media Type**\n"
                                f"{drive.media_type}\n\n"
                                f"**Protocol**\n"
                                f"{drive.protocol}\n\n"
                                f"**RPM**\n"
                                f"{drive.rpm if drive.rpm is not None else 'N/A'}\n\n"
                                f"**Health**\n"
                                f"{drive.health}\n\n"
                                f"**RAID Status**\n"
                                f"{drive.raid_status}\n\n"
                                f"**Predictive Failure**\n"
                                f"{'Yes' if drive.predictive_failure else 'No'}"
                                + self._life_line(drive)
                            ),
                        )

                    drives_state[drive_key] = current
                    state.save()

                elif previous_raw != current:
                    # Silently migrate the old dictionary representation
                    # to the new normalized state.
                    drives_state[drive_key] = current
                    state.save()

        finally:
            # Always close the Redfish session, even if the storage
            # request or processing above raises an exception.
            await client.close()

    async def _check_stale(self):
        """
        Determine whether storage monitoring has become stale.

        A stale notification is sent only once. Further failures remain
        silent until a successful storage check occurs.
        """

        # If storage has never successfully been checked, there is no
        # known state that can meaningfully become stale.
        last_success = state.get_last_success(
            self.MONITOR_NAME
        )

        if last_success is None:
            return

        # Prevent notification spam while the monitor remains offline.
        if state.is_marked_stale(
            self.MONITOR_NAME
        ):
            return

        # Check whether the last successful storage check is old enough
        # to exceed the configured stale threshold.
        if not state.is_stale(
            self.MONITOR_NAME,
            settings.MONITOR_STALE_DURATION,
        ):
            return

        await self.notification_service.warning(
            title="Storage Monitoring Stale",
            description=(
                "Aegis-Bot has been unable to retrieve current storage "
                "information from iDRAC.\n\n"
                f"**Last Successful Check**\n"
                f"`{last_success}`\n\n"
                f"**Stale After**\n"
                f"`{self._format_duration(settings.MONITOR_STALE_DURATION)}`\n\n"
                "Previously recorded storage states have been left "
                "unchanged until current data can be retrieved."
            ),
        )

        # Mark the monitor stale so subsequent failed checks do not
        # generate repeated notifications.
        state.mark_stale(
            self.MONITOR_NAME
        )

        state.save()

        logger.warning(
            "[StorageMonitor] Storage monitoring is now stale."
        )

    @staticmethod
    def _normalize_health(health) -> str:
        """
        Convert Redfish health values into Aegis-Bot's internal states.

        Redfish commonly reports "OK", while Aegis-Bot internally uses
        "healthy". Keeping one internal vocabulary prevents false state
        transitions and notifications.
        """

        if health is None:
            return "critical"

        value = str(health).strip().lower()

        if value in {
            "ok",
            "healthy",
            "online",
            "ready",
            "optimal",
        }:
            return "healthy"

        if value in {
            "warning",
            "degraded",
            "rebuilding",
            "non-raid",
        }:
            return "warning"

        if value in {
            "critical",
            "failed",
            "failure",
            "offline",
            "unavailable",
        }:
            return "critical"

        # Unknown health values are treated conservatively.
        logger.warning(
            "[StorageMonitor] Unknown storage health value: %s",
            health,
        )

        return "critical"

    @classmethod
    def _normalize_previous_state(cls, previous):
        """
        Convert persisted storage states from both the current and
        older formats into the internal state vocabulary.

        Current format:
            "healthy"

        Older format:
            {
                "health": "healthy",
                "raid_status": "Online"
            }

        This migration is intentionally silent when the effective
        health has not actually changed.
        """

        if previous is None:
            return None

        # Current state format.
        if isinstance(previous, str):
            return cls._normalize_health(
                previous
            )

        # Legacy volume/drive state format.
        if isinstance(previous, dict):
            health = previous.get(
                "health"
            )

            raid_status = previous.get(
                "raid_status"
            )

            predictive_failure = previous.get(
                "predictive_failure",
                False,
            )

            if predictive_failure:
                return "critical"

            health_state = cls._normalize_health(
                health
            )

            raid_state = cls._normalize_health(
                raid_status
            )

            # Critical takes priority.
            if (
                health_state == "critical"
                or raid_state == "critical"
            ):
                return "critical"

            # Warning takes priority over healthy.
            if (
                health_state == "warning"
                or raid_state == "warning"
            ):
                return "warning"

            return "healthy"

        return None

    @classmethod
    def _get_volume_status(
        cls,
        health: str,
        raid_status: str,
    ) -> str:
        """
        Determine the effective virtual-disk state.

        A volume is considered critical if either Redfish health or
        RAID status indicates a critical condition.
        """

        health_state = cls._normalize_health(
            health
        )

        raid_state = cls._normalize_health(
            raid_status
        )

        # Critical takes priority.
        if (
            health_state == "critical"
            or raid_state == "critical"
        ):
            return "critical"

        # Warning takes priority over healthy.
        if (
            health_state == "warning"
            or raid_state == "warning"
        ):
            return "warning"

        return "healthy"

    @staticmethod
    def ssd_life_warning() -> float:
        """SSD life left, in percent, at or below which a drive warns."""
        try:
            return float(os.getenv("SSD_LIFE_WARNING", "10"))
        except ValueError:
            return 10.0

    @staticmethod
    def _life_line(drive) -> str:
        life = getattr(drive, "media_life_left", None)

        if not isinstance(life, (int, float)):
            return ""

        return f"\n\n**SSD Life Left**\n{life:.0f}%"

    @classmethod
    def _get_drive_status(
        cls,
        health: str,
        raid_status: str,
        predictive_failure: bool,
        media_life_left: float | None = None,
    ) -> str:
        """
        Determine the effective physical-drive state.

        Predictive failure is treated as critical because it indicates
        that the drive has reported an impending hardware failure.
        """

        if predictive_failure:
            return "critical"

        # An SSD close to the end of its rated write endurance.
        if (
            isinstance(media_life_left, (int, float))
            and media_life_left <= cls.ssd_life_warning()
        ):
            return "warning"

        health_state = cls._normalize_health(
            health
        )

        raid_state = cls._normalize_health(
            raid_status
        )

        # Critical takes priority.
        if (
            health_state == "critical"
            or raid_state == "critical"
        ):
            return "critical"

        # Warning takes priority over healthy.
        if (
            health_state == "warning"
            or raid_state == "warning"
        ):
            return "warning"

        return "healthy"

    async def _notify_component(
        self,
        component: str,
        current: str,
        details: str,
    ):
        """
        Send a notification for a storage component state change.
        """

        if current == "healthy":
            await self.notification_service.recovered(
                title=f"Storage Recovered — {component}",
                description=details,
            )

        elif current == "warning":
            await self.notification_service.warning(
                title=f"Storage Warning — {component}",
                description=details,
            )

        elif current == "critical":
            await self.notification_service.critical(
                title=f"Storage Critical — {component}",
                description=details,
            )

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """
        Format a duration into a human-readable string.
        """

        minutes, remaining_seconds = divmod(
            seconds,
            60,
        )

        if minutes and remaining_seconds:
            return f"{minutes}m {remaining_seconds}s"

        if minutes:
            return f"{minutes} minutes"

        return f"{remaining_seconds} seconds"