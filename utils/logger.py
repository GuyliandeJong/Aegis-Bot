import logging
from logging.handlers import RotatingFileHandler
from config.paths import DATA_DIR


LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "aegis.log"


logger = logging.getLogger("Aegis-Bot")
logger.setLevel(logging.INFO)

logger.handlers.clear()


formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)


file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
)

file_handler.setFormatter(formatter)


logger.addHandler(console_handler)
logger.addHandler(file_handler)


logger.propagate = False


# discord.py reports errors raised inside commands, buttons and
# dropdowns on its own "discord" logger, which discards everything
# unless handlers are attached. Without this, a failing button simply
# stops responding and nothing is written anywhere.
discord_logger = logging.getLogger("discord")
discord_logger.setLevel(logging.WARNING)
discord_logger.addHandler(console_handler)
discord_logger.addHandler(file_handler)
discord_logger.propagate = False