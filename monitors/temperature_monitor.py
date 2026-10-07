from config.settings import settings
from monitors.base_monitor import BaseMonitor
from monitors.state import state
from services.temperature_service import TemperatureService
from utils.logger import logger
from utils.status import temperature_status


class TemperatureMonitor(BaseMonitor):
    """
    Monitors system temperature sensors.

    Each sensor is tracked independently.

    States:
        healthy
        warning
        critical

    Notifications are only generated when a sensor changes state.
    The first successful check establishes the initial state without
    sending an alert.

    A successful service request is tracked separately from sensor
    health so Aegis-Bot can distinguish between a known sensor state
    and a stale state caused by an unavailable iDRAC.
    """

    MONITOR_NAME = "temperature"

    def __init__(self, notification_service):
        self.service = TemperatureService()
        self.notification_service = notification_service

    async def check(self):
        """
        Check all available temperature sensors.

        If the temperature service cannot be reached, the previous
        sensor states remain unchanged. The monitor's last successful
        check timestamp is used to determine when those states become
        stale.
        """

        try:
            # Retrieve the current temperature information.
            #
            # If the Redfish request fails, this raises an exception.
            # MonitorManager handles that exception and the previous
            # successful-check timestamp remains unchanged.
            status = await self.service.get_temperature_status()

        except Exception as ex:
            # Do not modify any temperature health state when the
            # current data cannot be retrieved.
            #
            # The stale-state check below uses the previous successful
            # timestamp to determine whether an alert is necessary.
            logger.warning(
                "[TemperatureMonitor] Unable to retrieve temperature "
                "information: %s",
                ex,
            )

            await self._check_stale()

            return

        # The temperature service successfully returned data.
        #
        # Only now do we update the successful-check timestamp.
        was_stale = state.is_marked_stale(
            self.MONITOR_NAME
        )

        state.mark_success(
            self.MONITOR_NAME
        )

        state.save()

        # If the monitor was previously reported as stale, the
        # successful request means temperature monitoring has recovered.
        if was_stale:
            await self.notification_service.recovered(
                title="Temperature Monitoring Recovered",
                description=(
                    "Aegis-Bot has successfully retrieved temperature "
                    "information from iDRAC again.\n\n"
                    f"**Stale Duration**\n"
                    f"`{settings.MONITOR_STALE_DURATION // 60} minutes`"
                ),
            )

            logger.info(
                "[TemperatureMonitor] Temperature monitoring recovered."
            )

        for sensor in status.temperatures:
            current = temperature_status(
                sensor.reading
            ).lower()

            # temperature_status() reports a normal reading as "OK",
            # while this monitor's states are healthy/warning/critical.
            if current == "ok":
                current = "healthy"

            # Use the sensor name as the persistent state key.
            sensor_key = sensor.name

            previous = state.temperatures.get(
                sensor_key
            )

            # Older versions persisted "ok". Convert it silently so the
            # rename does not look like a recovery.
            if previous == "ok":
                previous = "healthy"
                state.temperatures[sensor_key] = previous

            # No state transition means there is nothing to report.
            if previous == current:
                continue

            alert_key = f"temperature:{sensor_key}"

            # A reading hovering around a threshold would otherwise
            # send a notification on every crossing. Stay quiet until
            # the cooldown has passed; the reported state is left
            # unchanged, so a change that persists is still reported
            # afterwards. A sensor becoming critical is always reported.
            if (
                previous is not None
                and current != "critical"
                and not state.alert_allowed(
                    alert_key,
                    self.alert_cooldown(),
                )
            ):
                logger.debug(
                    "[TemperatureMonitor] %s: %s -> %s suppressed "
                    "by alert cooldown.",
                    sensor.name,
                    previous,
                    current,
                )
                continue

            logger.info(
                "[TemperatureMonitor] %s: %s -> %s (%.1f°C)",
                sensor.name,
                previous or "unknown",
                current,
                sensor.reading,
            )

            # The first successful check establishes the initial state
            # silently.
            if previous is None:
                state.temperatures[sensor_key] = current
                state.save()
                continue

            # --------------------------------------------------------
            # RECOVERED
            # --------------------------------------------------------

            if current == "healthy":
                await self.notification_service.recovered(
                    title=f"Temperature Recovered — {sensor.name}",
                    description=(
                        f"**Sensor**\n"
                        f"{sensor.name}\n\n"
                        f"**Current Temperature**\n"
                        f"{sensor.reading:.1f}°C\n\n"
                        f"**Warning Threshold**\n"
                        f"{settings.TEMPERATURE_WARNING:.1f}°C\n\n"
                        f"**Critical Threshold**\n"
                        f"{settings.TEMPERATURE_CRITICAL:.1f}°C"
                    ),
                )

            # --------------------------------------------------------
            # WARNING
            # --------------------------------------------------------

            elif current == "warning":
                await self.notification_service.warning(
                    title=f"High Temperature — {sensor.name}",
                    description=(
                        f"**Sensor**\n"
                        f"{sensor.name}\n\n"
                        f"**Current Temperature**\n"
                        f"{sensor.reading:.1f}°C\n\n"
                        f"**Warning Threshold**\n"
                        f"{settings.TEMPERATURE_WARNING:.1f}°C\n\n"
                        f"**Critical Threshold**\n"
                        f"{settings.TEMPERATURE_CRITICAL:.1f}°C"
                    ),
                )

            # --------------------------------------------------------
            # CRITICAL
            # --------------------------------------------------------

            elif current == "critical":
                await self.notification_service.critical(
                    title=f"Critical Temperature — {sensor.name}",
                    description=(
                        f"**Sensor**\n"
                        f"{sensor.name}\n\n"
                        f"**Current Temperature**\n"
                        f"{sensor.reading:.1f}°C\n\n"
                        f"**Critical Threshold**\n"
                        f"{settings.TEMPERATURE_CRITICAL:.1f}°C"
                    ),
                )

            # Persist the new state after handling the transition.
            state.temperatures[sensor_key] = current
            state.mark_alert(alert_key)
            state.save()

    async def _check_stale(self):
        """
        Determine whether temperature monitoring has become stale.

        A stale notification is only sent once. Subsequent failed checks
        remain silent until a successful temperature check restores
        monitoring.
        """

        # A monitor that has never successfully checked temperature
        # data has no known state to classify as stale.
        last_success = state.get_last_success(
            self.MONITOR_NAME
        )

        if last_success is None:
            return

        # Do not repeatedly notify about the same stale condition.
        if state.is_marked_stale(
            self.MONITOR_NAME
        ):
            return

        # Check whether the previous successful check is now older
        # than the configured stale threshold.
        if not state.is_stale(
            self.MONITOR_NAME,
            settings.MONITOR_STALE_DURATION,
        ):
            return

        # The stored temperature state can no longer be considered
        # current because iDRAC has not provided a successful update
        # for the configured amount of time.
        await self.notification_service.warning(
            title="Temperature Monitoring Stale",
            description=(
                "Aegis-Bot has been unable to retrieve current "
                "temperature information from iDRAC.\n\n"
                f"**Last Successful Check**\n"
                f"`{last_success}`\n\n"
                f"**Stale After**\n"
                f"`{settings.MONITOR_STALE_DURATION // 60} minutes`\n\n"
                "Previously recorded temperature states have been "
                "left unchanged until current data can be retrieved."
            ),
        )

        # Record that the stale notification has already been sent.
        state.mark_stale(
            self.MONITOR_NAME
        )

        state.save()

        logger.warning(
            "[TemperatureMonitor] Temperature monitoring is now stale."
        )