from abc import ABC, abstractmethod


class BaseMonitor(ABC):
    @abstractmethod
    async def check(self):
        """Perform one monitoring cycle."""
        pass