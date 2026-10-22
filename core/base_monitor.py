from abc import ABC, abstractmethod

from config.settings import settings


class BaseMonitor(ABC):
    # Used when ALERT_COOLDOWN is not present in config/settings.py.
    DEFAULT_ALERT_COOLDOWN = 30 * 60

    @classmethod
    def alert_cooldown(cls) -> int:
        """
        Seconds a component must wait before it may alert again.

        This stops a value hovering around a threshold from sending a
        notification every time it crosses it.
        """
        return int(
            getattr(
                settings,
                "ALERT_COOLDOWN",
                cls.DEFAULT_ALERT_COOLDOWN,
            )
        )

    @abstractmethod
    async def check(self):
        """Perform one monitoring cycle."""
        pass