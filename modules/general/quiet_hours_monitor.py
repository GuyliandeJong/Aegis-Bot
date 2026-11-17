"""Posts the quiet hours summary when the quiet hours are over."""

from core import quiet_hours


class QuietHoursMonitor:
    """
    Alerts held during QUIET_HOURS are posted as one summary when the
    window ends. NotificationService also does this before the next
    alert; this monitor makes it happen when no alert follows.

    .env:
        QUIET_HOURS  a daily window such as 23-7 or 22:30-07:00, in the
                     container's time zone (empty: off)
    """

    INTERVAL = 60

    def __init__(self, notification_service):
        self.notification_service = notification_service

    async def check(self):
        await self.notification_service.flush_quiet_hours()
