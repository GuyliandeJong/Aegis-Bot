"""qBittorrent downloads, Jackett search, approvals, the watchlist and seeding cleanup."""

from core.modules import Module


MODULE = Module(
    name="torrents",
    description="qBittorrent downloads, Jackett search, approvals, the watchlist and seeding cleanup.",
    required=(
        "QBITTORRENT_HOST",
        "QBITTORRENT_USERNAME",
        "QBITTORRENT_PASSWORD",
    ),
    monitors=(
        "modules.torrents.torrent_monitor:TorrentMonitor",
        "modules.torrents.torrent_health_monitor:TorrentHealthMonitor",
        "modules.torrents.watchlist_monitor:WatchlistMonitor",
        "modules.torrents.seed_cleanup_monitor:SeedCleanupMonitor",
        "modules.torrents.vpn_monitor:VpnMonitor",
    ),
    setup="modules.torrents.torrent_action_tracker:install_qbittorrent_action_tracking",
    startup_checks="modules.torrents.checks:checks",
    shutdown="modules.torrents.qbittorrent_service:QBittorrentService.shutdown",
)
