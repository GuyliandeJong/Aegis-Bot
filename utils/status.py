from utils import theme
from config.settings import settings


def health_icon(health: str) -> str:
    """
    Return an icon representing Redfish health.
    """

    return {
        "OK": theme.SUCCESS_EMOJI,
        "Warning": theme.WARNING_EMOJI,
        "Critical": theme.ERROR_EMOJI,
    }.get(health, "⚪")


def health_text(health: str) -> str:
    """
    Return a friendly health description.
    """

    return {
        "OK": "Healthy",
        "Warning": "Warning",
        "Critical": "Critical",
    }.get(health, health)


def status_icon(status: str) -> str:
    """
    Return an icon for RAID or operational status.
    """

    status = status.lower()

    if status == "online":
        return theme.SUCCESS_EMOJI

    if "rebuild" in status:
        return theme.WARNING_EMOJI

    if status in (
        "degraded",
        "failed",
        "offline",
        "foreign",
    ):
        return theme.ERROR_EMOJI

    return "⚪"


def usage_icon(percent: int) -> str:
    """
    Return an icon based on percentage used.
    """

    if percent >= 90:
        return theme.ERROR_EMOJI

    if percent >= 75:
        return theme.WARNING_EMOJI

    return theme.SUCCESS_EMOJI

def temperature_status(reading: float) -> str:
    """
    Return the Aegis-Bot temperature status based on
    the configured temperature thresholds.
    """

    if reading >= settings.TEMPERATURE_CRITICAL:
        return "Critical"

    if reading >= settings.TEMPERATURE_WARNING:
        return "Warning"

    return "OK"