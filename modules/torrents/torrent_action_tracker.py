import time
from functools import wraps


_ACTION_TTL = 90
_expected_actions = {}

_PAUSED_STATES = {
    "pauseddl",
    "pausedup",
    "stoppeddl",
    "stoppedup",
}

_ACTIVE_STATES = {
    "downloading",
    "stalleddl",
    "forceddl",
    "metadl",
}

_QUEUED_DOWNLOAD_STATES = {
    "queueddl",
}


def _record_action(torrent_hash, action):
    key = str(torrent_hash).lower()
    _expected_actions[key] = {
        "action": action,
        "expires_at": time.time() + _ACTION_TTL,
    }


def consume_expected_action(torrent_hash, observed_state):
    """Consume a recent matching pause/resume action made through the bot."""
    key = str(torrent_hash).lower()
    expected = _expected_actions.get(key)

    if expected is None:
        return None

    if expected["expires_at"] < time.time():
        _expected_actions.pop(key, None)
        return None

    state = str(observed_state or "").lower()
    action = expected["action"]

    if action == "pause" and state in _PAUSED_STATES:
        _expected_actions.pop(key, None)
        return action

    if action == "resume" and (
        state in _ACTIVE_STATES
        or state in _QUEUED_DOWNLOAD_STATES
    ):
        _expected_actions.pop(key, None)
        return action

    return None


def install_qbittorrent_action_tracking():
    """Mark successful pause/resume calls made by any bot command."""
    from modules.torrents.qbittorrent_service import QBittorrentService

    for method_name, action in (
        ("pause_torrent", "pause"),
        ("resume_torrent", "resume"),
    ):
        original = getattr(QBittorrentService, method_name)

        if getattr(original, "_aegis_action_tracker", False):
            continue

        @wraps(original)
        async def tracked_method(
            service,
            torrent_hash,
            *args,
            _original=original,
            _action=action,
            **kwargs,
        ):
            result = await _original(
                service,
                torrent_hash,
                *args,
                **kwargs,
            )
            _record_action(torrent_hash, _action)
            return result

        tracked_method._aegis_action_tracker = True
        setattr(
            QBittorrentService,
            method_name,
            tracked_method,
        )