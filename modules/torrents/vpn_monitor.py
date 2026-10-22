"""Alerts when qBittorrent may be torrenting without its VPN."""

import json
import os

from config.paths import DATA_DIR
from config.settings import settings
from core.base_monitor import BaseMonitor
from core.state import state
from modules.torrents.qbittorrent_service import QBittorrentService
from modules.wan.wan_service import WanService
from utils.logger import logger


class VpnMonitor(BaseMonitor):
    """
    Checks every two minutes that qBittorrent is behind its VPN.

    qBittorrent runs in the qbittorrentvpn container; when the tunnel
    fails it may connect through the home line instead.

    Leak: qBittorrent 5.1 and later report the external IPv4 address
    peers and trackers see (`last_external_address_v4` in
    /transfer/info). When it equals the home WAN IP, a critical
    "Torrents Not Behind VPN" is posted once, and "Recovered" when the
    address differs again. Older versions do not have the field; then
    only the connection status is watched, and that is logged once.

    Connection: a warning when qBittorrent reports "disconnected"
    (not listening) for FAILURES_BEFORE_ALERT checks in a row, which
    usually means the tunnel is down; "Recovered" when it connects.

    Addresses in alerts show only the first two octets.

    .env:
        VPN_CHECK   true (default) or false. Does nothing when
                    qBittorrent is not configured.
    """

    STATE_FILE = DATA_DIR / "vpn_monitor_state.json"

    INTERVAL = 120
    TIMEOUT = 60

    # "disconnected" checks in a row before it is reported.
    FAILURES_BEFORE_ALERT = 3

    def __init__(self, notification_service):
        self.notification_service = notification_service
        self.state = {"leak": False, "disconnected": 0, "down": False}

        # The missing address field is logged once per start, not
        # every two minutes.
        self.leak_check_logged = False

        self.load_state()

    @staticmethod
    def enabled() -> bool:
        value = os.getenv("VPN_CHECK", "true")
        return value.strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def configured() -> bool:
        return bool(getattr(settings, "QBITTORRENT_HOST", ""))

    def load_state(self):
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())

                if isinstance(data, dict):
                    self.state.update(data)
        except Exception as ex:
            logger.error("[VpnMonitor] Failed to load state: %s", ex)

    def save_state(self):
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=4))
            temporary.replace(self.STATE_FILE)
        except Exception as ex:
            logger.error("[VpnMonitor] Failed to save state: %s", ex)

    @staticmethod
    def mask(address: str) -> str:
        """203.0.113.7 -> 203.0.x.x, so no full address reaches Discord."""
        parts = str(address).split(".")

        if len(parts) != 4:
            return "unknown"

        return f"{parts[0]}.{parts[1]}.x.x"

    @staticmethod
    async def home_ip() -> str | None:
        """
        The home public IPv4 address.

        The WAN monitor keeps it current every minute; it is asked
        directly only before the WAN monitor has recorded one.
        """
        ip = state.wan.get("ip")

        if ip:
            return ip

        try:
            return await WanService().get_ip() or None
        except Exception as ex:
            logger.warning("[VpnMonitor] Unable to retrieve the WAN IP: %s", ex)
            return None

    @staticmethod
    def verdict(transfer: dict, home_ip: str | None) -> str:
        """
        "ok", "leak" or "unknown" for a /transfer/info answer.

        Unknown when this qBittorrent version has no address field,
        when it has not learned its address yet (empty), or when the
        home IP is not known.
        """
        external = (transfer or {}).get("last_external_address_v4")

        if not external or not home_ip:
            return "unknown"

        return "leak" if external.strip() == home_ip.strip() else "ok"

    async def check(self):
        if not self.enabled() or not self.configured():
            return

        service = QBittorrentService()

        try:
            transfer = await service.get_transfer_info()
        except Exception as ex:
            # qBittorrent being down is not a VPN problem; the torrent
            # monitors and the startup check report that.
            logger.warning("[VpnMonitor] qBittorrent not reachable: %s", ex)
            return
        finally:
            await service.close()

        if not isinstance(transfer, dict):
            logger.warning("[VpnMonitor] Unexpected /transfer/info answer.")
            return

        await self._leak(transfer)
        await self._connection(transfer)

        self.save_state()

    async def _leak(self, transfer: dict) -> None:
        if "last_external_address_v4" not in transfer:
            if not self.leak_check_logged:
                self.leak_check_logged = True
                logger.info(
                    "[VpnMonitor] This qBittorrent version does not report "
                    "its external address (added in 5.1); only the "
                    "connection status is checked."
                )
            return

        home_ip = await self.home_ip()
        verdict = self.verdict(transfer, home_ip)

        if verdict == "unknown":
            return

        external = transfer["last_external_address_v4"]
        addresses = (
            f"**qBittorrent's address**\n`{self.mask(external)}`\n\n"
            f"**Home address**\n`{self.mask(home_ip)}`"
        )

        if verdict == "leak" and not self.state["leak"]:
            self.state["leak"] = True
            logger.error("[VpnMonitor] qBittorrent uses the home WAN address.")
            await self.notification_service.critical(
                title="Torrents Not Behind VPN",
                description=(
                    "qBittorrent reports the home internet address as its "
                    "external address: torrents are not going through the "
                    "VPN. Check the qbittorrentvpn container.\n\n"
                    f"{addresses}"
                ),
            )
        elif verdict == "ok" and self.state["leak"]:
            self.state["leak"] = False
            logger.info("[VpnMonitor] qBittorrent is behind the VPN again.")
            await self.notification_service.recovered(
                title="Torrents Behind VPN Again",
                description=(
                    "qBittorrent's external address is no longer the home "
                    f"address.\n\n{addresses}"
                ),
            )

    async def _connection(self, transfer: dict) -> None:
        status = str(transfer.get("connection_status", "")).lower()

        if status == "disconnected":
            self.state["disconnected"] += 1

            if (
                self.state["disconnected"] >= self.FAILURES_BEFORE_ALERT
                and not self.state["down"]
            ):
                self.state["down"] = True
                await self.notification_service.warning(
                    title="qBittorrent Disconnected",
                    description=(
                        f"qBittorrent has reported no connection for "
                        f"{self.state['disconnected']} checks in a row "
                        f"(every {self.INTERVAL // 60} minutes). The VPN "
                        "tunnel of the qbittorrentvpn container is the "
                        "usual cause."
                    ),
                )
            return

        if status not in {"connected", "firewalled"}:
            return

        if self.state["down"]:
            await self.notification_service.recovered(
                title="qBittorrent Connected Again",
                description=f"qBittorrent reports **{status}** again.",
            )

        self.state["disconnected"] = 0
        self.state["down"] = False
