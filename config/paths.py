"""
Where the bot keeps its files.

DATA_DIR holds state, logs and remembered messages; it must survive
updates, so in Docker it is a volume (/app/data by default). Set
DATA_DIR in the environment to keep it elsewhere, for example when
running the bot without Docker.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# Read .env here too: this module is imported before config.settings.
load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

# Files shipped with the bot, such as the default torrent preferences.
CONFIG_DIR = Path(__file__).resolve().parent


def user_file(name: str) -> Path:
    """
    A configuration file people may change: their copy in DATA_DIR
    when there is one, otherwise the default in config/.
    """
    own = DATA_DIR / name
    return own if own.exists() else CONFIG_DIR / name
