"""Posts the end of maintenance on time, also when no alerts arrive."""

from services import maintenance


class MaintenanceMonitor:
    """
    Ends maintenance once its time has run out.

    Every alert already checks this on the way out, but a quiet
    maintenance window would otherwise only be reported as ended with
    the next alert. Reading the state is a dictionary lookup, so the
    check is cheap enough to run every minute.
    """

    INTERVAL = 60

    def __init__(self, notification_service):
        self.notification_service = notification_service

    async def check(self):
        if maintenance.expired():
            await self.notification_service.end_maintenance_if_expired()
