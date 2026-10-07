from datetime import datetime, timedelta

from utils import theme


def format_capacity(bytes_value: int) -> str:
    """
    Convert bytes into a readable binary unit.
    """

    tib = bytes_value / (1024 ** 4)
    gib = bytes_value / (1024 ** 3)
    mib = bytes_value / (1024 ** 2)

    if tib >= 1:
        return f"{tib:.2f} TiB"

    if gib >= 1:
        return f"{gib:.0f} GiB"

    return f"{mib:.0f} MiB"


def usage_bar(percent: int) -> str:
    """
    Return a 10-block usage bar using the Aegis-Bot theme.
    """

    # Keep the percentage within the valid range.
    percent = max(0, min(100, percent))

    filled = round(percent / 10)

    if percent >= 90:
        full = theme.USAGE_CRITICAL_BLOCK
    elif percent >= 75:
        full = theme.USAGE_WARNING_BLOCK
    else:
        full = theme.USAGE_HEALTHY_BLOCK

    return (
        full * filled
        + theme.USAGE_EMPTY_BLOCK * (10 - filled)
    )


def format_duration(seconds: int) -> str:
    """
    Convert seconds into a readable duration.
    """

    return str(timedelta(seconds=seconds))


def format_uptime(boot_time: float) -> str:
    """
    Return a compact uptime string from a boot timestamp.
    """

    uptime = datetime.now() - datetime.fromtimestamp(boot_time)

    days = uptime.days
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)


def format_bytes(value: int | float | None) -> str:
    """
    Format a byte value into a readable binary size, e.g. "1.50 GiB".
    """

    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0.0

    if value < 1024:
        return f"{value:.0f} B"

    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024

        if value < 1024:
            return f"{value:.2f} {unit}"

    return f"{value:.2f} EiB"


def format_compact_duration(seconds: int | float | None) -> str:
    """
    Format seconds as a compact duration, e.g. "1d 2h 5m 30s".
    """

    try:
        seconds = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        seconds = 0

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)
