from datetime import datetime, timezone

from config.settings import settings
from monitors.base_monitor import BaseMonitor
from monitors.state import state
from services import activity_log, wan_recovery
from services.wan_service import WanService
from utils.logger import logger


class WanMonitor(BaseMonitor):
    """
    Monitors internet connectivity and public WAN IP changes.

    Internet connectivity:
        Requires multiple external endpoints to fail before an
        outage is recorded.

    Outage:
        Must remain unavailable for WAN_OUTAGE_DURATION before
        generating a notification.

    Recovery:
        A notification is sent when connectivity returns after
        a confirmed outage.

    Public IP:
        Changes are tracked separately from internet connectivity.
    """

    def __init__(self, notification_service):
        self.service = WanService()
        self.notification_service = notification_service

    @staticmethod
    def _now() -> datetime:
        """
        Return the current UTC time.
        """

        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_timestamp(value):
        """
        Convert a stored ISO timestamp into a datetime.
        """

        if not value:
            return None

        return datetime.fromisoformat(value)

    @staticmethod
    def _timestamp(value):
        """
        Convert a datetime into a persistent ISO timestamp.
        """

        return value.isoformat() if value else None

    async def check(self):
        """
        Check internet connectivity and public WAN IP.
        """

        wan_state = state.wan

        current_time = self._now()

        previous_connected = wan_state.get(
            "connected"
        )

        outage_since = self._parse_timestamp(
            wan_state.get("outage_since")
        )

        previous_ip = wan_state.get(
            "ip"
        )

        # --------------------------------------------------------
        # INTERNET CONNECTIVITY
        # --------------------------------------------------------

        connected = await self.service.is_connected()

        logger.debug(
            "[WanMonitor] Internet connectivity: %s",
            "online" if connected else "offline",
        )

        # --------------------------------------------------------
        # CONNECTION AVAILABLE
        # --------------------------------------------------------

        if connected:

            # A previously confirmed outage has recovered.
            if (
                previous_connected is False
                and outage_since is not None
            ):
                downtime = (
                    current_time - outage_since
                ).total_seconds()

                minutes = int(downtime // 60)
                seconds = int(downtime % 60)

                await self.notification_service.recovered(
                    title="WAN Connection Recovered",
                    description=(
                        f"Internet connectivity has been "
                        f"restored.\n\n"
                        f"**Downtime**\n"
                        f"`{minutes}m {seconds}s`"
                    ),
                )

                logger.info(
                    "[WanMonitor] WAN connection recovered "
                    "after %sm %ss",
                    minutes,
                    seconds,
                )

                # For the weekly summary.
                activity_log.record(
                    "wan_outage",
                    seconds=int(downtime),
                )

            # Containers such as newt need a restart after an outage,
            # also one too short to be reported; WAN_RECOVERY_RESTART.
            if outage_since is not None:
                wan_recovery.start(
                    self.notification_service,
                    (current_time - outage_since).total_seconds(),
                )

            # Clear the outage timer.
            wan_state["connected"] = True
            wan_state["outage_since"] = None

            state.save()

        # --------------------------------------------------------
        # CONNECTION UNAVAILABLE
        # --------------------------------------------------------

        else:

            # Start the outage timer if this is the first failed
            # connectivity check.
            if outage_since is None:

                outage_since = current_time

                wan_state["outage_since"] = self._timestamp(
                    outage_since
                )

                logger.warning(
                    "[WanMonitor] WAN connectivity check failed. "
                    "Outage timer started."
                )

                state.save()

                return

            duration = (
                current_time - outage_since
            ).total_seconds()

            # The outage has not lasted long enough yet.
            if duration < settings.WAN_OUTAGE_DURATION:

                logger.debug(
                    "[WanMonitor] WAN outage ongoing: %.0fs / %ss",
                    duration,
                    settings.WAN_OUTAGE_DURATION,
                )

                return

            # Don't repeatedly report the same outage.
            if previous_connected is False:

                return

            # The outage has now been confirmed.
            await self.notification_service.critical(
                title="WAN Connection Lost",
                description=(
                    f"Internet connectivity has been "
                    f"unavailable for at least "
                    f"{settings.WAN_OUTAGE_DURATION // 60} minutes."
                ),
            )

            wan_state["connected"] = False

            state.save()

            logger.error(
                "[WanMonitor] WAN connection confirmed lost "
                "after %.0f seconds.",
                duration,
            )

            return

        # --------------------------------------------------------
        # PUBLIC IP
        # --------------------------------------------------------

        # Only attempt the public IP lookup when internet
        # connectivity is confirmed.
        try:
            current_ip = await self.service.get_ip()

        except Exception as ex:

            # Failure to retrieve the public IP does NOT mean
            # internet connectivity has been lost.
            logger.warning(
                "[WanMonitor] Unable to retrieve public WAN IP: %s",
                ex,
            )

            return

        # First successful lookup establishes the baseline.
        if previous_ip is None:

            wan_state["ip"] = current_ip

            state.save()

            logger.info(
                "[WanMonitor] Initial public WAN IP recorded: %s",
                current_ip,
            )

            return

        # Public IP has changed.
        if previous_ip != current_ip:

            await self.notification_service.info(
                title="Public WAN IP Changed",
                description=(
                    f"Your public WAN IP has changed.\n\n"
                    f"**Previous IP**\n"
                    f"`{previous_ip}`\n\n"
                    f"**Current IP**\n"
                    f"`{current_ip}`"
                ),
            )

            logger.info(
                "[WanMonitor] WAN IP changed: %s -> %s",
                previous_ip,
                current_ip,
            )

            wan_state["ip"] = current_ip

            state.save()