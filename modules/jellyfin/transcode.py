"""Words for Jellyfin's transcode reasons, shared by /jellyfin playing and the transcode alert."""

import re


# Jellyfin's transcode reasons, as people would say them.
REASONS = {
    "ContainerNotSupported": "container",
    "VideoCodecNotSupported": "video codec",
    "AudioCodecNotSupported": "audio codec",
    "SubtitleCodecNotSupported": "subtitles burned in",
    "AudioIsExternal": "external audio",
    "SecondaryAudioNotSupported": "second audio track",
    "VideoResolutionNotSupported": "resolution",
    "VideoBitrateNotSupported": "video bitrate",
    "AudioBitrateNotSupported": "audio bitrate",
    "ContainerBitrateExceedsLimit": "bitrate limit",
    "VideoRangeTypeNotSupported": "HDR",
    "AudioChannelsNotSupported": "audio channels",
}


def describe_reason(reason: str) -> str:
    """A known reason in plain words; an unknown one split into words."""
    if reason in REASONS:
        return REASONS[reason]

    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(reason)).lower()


def describe_reasons(reasons) -> list[str]:
    return [describe_reason(reason) for reason in reasons or []]
