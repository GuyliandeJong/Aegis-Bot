"""Discord UI views for the torrent command."""

from commands.torrent.views.control_view import TorrentControlView
from commands.torrent.views.delete_view import TorrentDeleteConfirmationView
from commands.torrent.views.list_view import TorrentListView


__all__ = [
    "TorrentListView",
    "TorrentControlView",
    "TorrentDeleteConfirmationView",
]