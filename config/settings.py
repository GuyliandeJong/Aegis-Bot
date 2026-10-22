import os

from dotenv import load_dotenv


# Load configuration values from the .env file.
load_dotenv()


class Settings:
    """
    Centralized application configuration.

    Values are loaded from environment variables so they can be
    changed through the .env file without modifying application code.

    It contains no addresses, names or credentials: anything specific
    to one installation is set in .env. Only the Discord values are
    required; every other feature is a module that stays off until its
    settings are set (see core/modules.py).
    """

    def __init__(self):
        # Names of required .env values that were not set. bot.py
        # refuses to start without them.
        self.MISSING: list[str] = []

        # --------------------------------------------------------
        # DISCORD
        # --------------------------------------------------------

        # The bot's token, from the Discord developer portal.
        self.DISCORD_TOKEN = self._required("DISCORD_TOKEN")

        # The server (guild) the slash commands are registered in.
        self.GUILD_ID = self._required_id("GUILD_ID")

        # --------------------------------------------------------
        # iDRAC
        # --------------------------------------------------------

        # IP address or hostname of the Dell iDRAC interface.
        self.IDRAC_HOST = self._optional("IDRAC_HOST")

        # iDRAC login username.
        self.IDRAC_USERNAME = self._optional("IDRAC_USERNAME")

        # iDRAC login password.
        self.IDRAC_PASSWORD = self._optional("IDRAC_PASSWORD")

        # --------------------------------------------------------
        # FILESYSTEM MONITORING
        # --------------------------------------------------------

        # Filesystem usage percentage at which a warning is triggered.
        self.FILESYSTEM_WARNING = int(
            os.getenv("FILESYSTEM_WARNING", "85")
        )

        # Filesystem usage percentage at which a critical alert is triggered.
        self.FILESYSTEM_CRITICAL = int(
            os.getenv("FILESYSTEM_CRITICAL", "95")
        )

        # --------------------------------------------------------
        # TEMPERATURE MONITORING
        # --------------------------------------------------------

        # Temperature at which a warning is triggered.
        self.TEMPERATURE_WARNING = float(
            os.getenv("TEMPERATURE_WARNING", "70")
        )

        # Temperature at which a critical alert is triggered.
        self.TEMPERATURE_CRITICAL = float(
            os.getenv("TEMPERATURE_CRITICAL", "85")
        )

        # --------------------------------------------------------
        # CPU MONITORING
        # --------------------------------------------------------

        # CPU usage percentage at which the warning timer starts.
        self.CPU_WARNING = float(
            os.getenv("CPU_WARNING", "75")
        )

        # CPU usage percentage at which the critical timer starts.
        self.CPU_CRITICAL = float(
            os.getenv("CPU_CRITICAL", "90")
        )

        # How long CPU usage must remain at or above the warning
        # threshold before a warning notification is sent.
        # Value is in seconds.
        self.CPU_WARNING_DURATION = int(
            os.getenv("CPU_WARNING_DURATION", "300")
        )

        # How long CPU usage must remain at or above the critical
        # threshold before a critical notification is sent.
        # Value is in seconds.
        self.CPU_CRITICAL_DURATION = int(
            os.getenv("CPU_CRITICAL_DURATION", "300")
        )

        # CPU usage must remain below this threshold during recovery
        # before a warning condition can be considered recovered.
        self.CPU_RECOVERY_THRESHOLD = float(
            os.getenv("CPU_RECOVERY_THRESHOLD", "65")
        )

        # CPU usage must remain below this threshold during recovery
        # before a critical condition can move back to warning.
        self.CPU_CRITICAL_RECOVERY_THRESHOLD = float(
            os.getenv("CPU_CRITICAL_RECOVERY_THRESHOLD", "80")
        )

        # How long CPU usage must remain below the recovery threshold
        # before the CPU state is changed.
        # Value is in seconds.
        self.CPU_RECOVERY_DURATION = int(
            os.getenv("CPU_RECOVERY_DURATION", "120")
        )

        # --------------------------------------------------------
        # MEMORY MONITORING
        # --------------------------------------------------------

        # Memory usage percentage at which the warning timer starts.
        self.MEMORY_WARNING = float(
            os.getenv("MEMORY_WARNING", "80")
        )

        # Memory usage percentage at which the critical timer starts.
        self.MEMORY_CRITICAL = float(
            os.getenv("MEMORY_CRITICAL", "90")
        )

        # How long memory usage must remain at or above the warning
        # threshold before a warning notification is sent.
        # Value is in seconds.
        self.MEMORY_WARNING_DURATION = int(
            os.getenv("MEMORY_WARNING_DURATION", "300")
        )

        # How long memory usage must remain at or above the critical
        # threshold before a critical notification is sent.
        # Value is in seconds.
        self.MEMORY_CRITICAL_DURATION = int(
            os.getenv("MEMORY_CRITICAL_DURATION", "300")
        )

        # Memory usage must remain below this threshold during recovery
        # before a warning condition can be considered recovered.
        self.MEMORY_RECOVERY_THRESHOLD = float(
            os.getenv("MEMORY_RECOVERY_THRESHOLD", "75")
        )

        # Memory usage must remain below this threshold during recovery
        # before a critical condition can move back to warning.
        self.MEMORY_CRITICAL_RECOVERY_THRESHOLD = float(
            os.getenv("MEMORY_CRITICAL_RECOVERY_THRESHOLD", "85")
        )

        # How long memory usage must remain below the recovery threshold
        # before the memory state is changed.
        # Value is in seconds.
        self.MEMORY_RECOVERY_DURATION = int(
            os.getenv("MEMORY_RECOVERY_DURATION", "120")
        )

        # --------------------------------------------------------
        # WAN MONITORING
        # --------------------------------------------------------

        # How long internet connectivity must be unavailable before
        # Aegis-Bot considers it an actual WAN outage.
        # Value is in seconds.
        self.WAN_OUTAGE_DURATION = int(
            os.getenv("WAN_OUTAGE_DURATION", "120")
        )

        # --------------------------------------------------------
        # MONITORING FRAMEWORK
        # --------------------------------------------------------

        # How often Aegis-Bot runs a complete monitoring cycle.
        # Value is in seconds.
        self.MONITOR_INTERVAL = int(
            os.getenv("MONITOR_INTERVAL", "60")
        )

        # Maximum amount of time a single monitor is allowed to run
        # before Aegis-Bot cancels it.
        # Value is in seconds.
        self.MONITOR_TIMEOUT = int(
            os.getenv("MONITOR_TIMEOUT", "30")
        )

        # How long a monitor may go without a successful check before
        # Aegis-Bot considers its stored component state stale.
        # Value is in seconds.
        self.MONITOR_STALE_DURATION = int(
            os.getenv("MONITOR_STALE_DURATION", "300")
        )

        # Minimum time between two filesystem or temperature alerts
        # for the same component, so a value hovering around a
        # threshold does not alert on every crossing. A component
        # becoming critical is always reported immediately.
        # Value is in seconds.
        self.ALERT_COOLDOWN = int(
            os.getenv("ALERT_COOLDOWN", "1800")
        )

        # --------------------------------------------------------
        # DISCORD
        # --------------------------------------------------------

        # Discord channel where monitoring notifications are sent.
        self.ALERT_CHANNEL_ID = self._required_id("ALERT_CHANNEL_ID")

        # Discord user mentioned in torrent notifications. Optional;
        # leave unset to send those notifications without a mention.
        self.DISCORD_USER_ID = os.getenv(
            "DISCORD_USER_ID",
            "",
        )

        # --------------------------------------------------------
        # GPTWOL
        # --------------------------------------------------------

        # IP address/hostname of the internal GPTWOL container.
        self.GPTWOL_HOST = self._optional("GPTWOL_HOST")

        # HTTP port exposed by GPTWOL.
        self.GPTWOL_PORT = int(
            os.getenv("GPTWOL_PORT", "5000")
        )

        # Maximum amount of time Aegis-Bot waits for GPTWOL to respond.
        # Value is in seconds.
        self.GPTWOL_TIMEOUT = int(
            os.getenv("GPTWOL_TIMEOUT", "10")
        )

        # Name of the computer, as shown in /wol responses.
        self.GPTWOL_TARGET_NAME = os.getenv(
            "GPTWOL_TARGET_NAME",
            "Computer",
        )

        # IP address GPTWOL uses to determine whether the computer
        # is currently awake.
        self.GPTWOL_TARGET_IP = self._optional("GPTWOL_TARGET_IP")

        # MAC address GPTWOL uses for Wake-on-LAN/Sleep-on-LAN.
        self.GPTWOL_TARGET_MAC = self._optional("GPTWOL_TARGET_MAC")

        # Status-check method configured in GPTWOL for this computer,
        # for example "arp" or "icmp".
        self.GPTWOL_TARGET_TEST_TYPE = os.getenv(
            "GPTWOL_TARGET_TEST_TYPE",
            "arp",
        )

        # --------------------------------------------------------
        # QBITTORRENT
        # --------------------------------------------------------

        # IP address/hostname of the qBittorrent WebUI.
        self.QBITTORRENT_HOST = self._optional("QBITTORRENT_HOST")

        # HTTP port of the qBittorrent WebUI.
        self.QBITTORRENT_PORT = int(
            os.getenv("QBITTORRENT_PORT", "8080")
        )

        # qBittorrent WebUI login.
        self.QBITTORRENT_USERNAME = self._optional(
            "QBITTORRENT_USERNAME"
        )
        self.QBITTORRENT_PASSWORD = self._optional(
            "QBITTORRENT_PASSWORD"
        )

        # Maximum amount of time Aegis-Bot waits for qBittorrent to
        # respond.
        # Value is in seconds.
        self.QBITTORRENT_TIMEOUT = int(
            os.getenv("QBITTORRENT_TIMEOUT", "10")
        )

    def _required(self, name: str) -> str:
        """
        Return a value the bot cannot run without.

        A missing value is recorded instead of raising, so bot.py can
        name every missing value at once.
        """
        value = os.getenv(name, "").strip()

        if not value:
            self.MISSING.append(name)

        return value

    def _required_id(self, name: str) -> int:
        """A required Discord ID: digits only, recorded as missing otherwise."""
        value = os.getenv(name, "").strip()

        if not value.isdigit():
            self.MISSING.append(name)
            return 0

        return int(value)

    @staticmethod
    def _optional(name: str) -> str:
        """A module's setting; its module stays off while it is empty."""
        return os.getenv(name, "").strip()


# Create one shared Settings instance.
#
# Other modules import `settings` from this file so the application
# uses the same configuration everywhere.
settings = Settings()
