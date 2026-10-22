"""Discord UI views for the torrent command."""

from modules.torrents.torrent.views.control_view import TorrentControlView
from modules.torrents.torrent.views.delete_view import TorrentDeleteConfirmationView
from modules.torrents.torrent.views.list_view import TorrentListView


__all__ = [
    "TorrentListView",
    "TorrentControlView",
    "TorrentDeleteConfirmationView",
]