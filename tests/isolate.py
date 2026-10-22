"""Point the bot's state files at a temporary folder of their own, so a check never writes to the real data folder. Import after the project modules."""
import sys, tempfile
from pathlib import Path
from config.paths import DATA_DIR as REAL
DATA = Path(tempfile.mkdtemp(prefix="aegis-data-"))
for name, attribute in [("utils.open_messages", "STATE_FILE"), ("modules.torrents.torrent_requesters", "STATE_FILE"), ("core.activity_log", "STATE_FILE"), ("modules.torrents.watchlist", "STATE_FILE"), ("modules.jellyfin.subtitle_fetch", "STATE_FILE"), ("core.maintenance", "STATE_FILE")]:
    module = sys.modules.get(name)
    if module is not None and str(getattr(module, attribute, "")).startswith(str(REAL)):
        setattr(module, attribute, DATA / Path(getattr(module, attribute)).name)
