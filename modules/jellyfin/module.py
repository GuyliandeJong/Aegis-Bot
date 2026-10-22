"""Jellyfin libraries: lookups, new content, subtitles and copies between servers."""

from core.modules import Module


MODULE = Module(
    name="jellyfin",
    description="Jellyfin libraries: lookups, new content, subtitles and copies between servers.",
    required=(
        "JELLYFIN_1_URL",
        "JELLYFIN_1_API_KEY",
    ),
    monitors=(
        "modules.jellyfin.new_content_monitor:JellyfinNewContentMonitor",
        "modules.jellyfin.subtitle_monitor:SubtitleMonitor",
    ),
    startup_checks="modules.jellyfin.checks:checks",
)
