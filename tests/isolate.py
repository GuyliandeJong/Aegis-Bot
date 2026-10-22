"""Point the bot's state files at a temporary folder, so a check never writes to /app/data. Import after the project modules."""
import sys, tempfile
from pathlib import Path
DATA = Path(tempfile.mkdtemp(prefix="aegis-data-"))
for name, attribute in [("utils.open_messages", "STATE_FILE"), ("modules.torrents.torrent_requesters", "STATE_FILE"), ("core.activity_log", "STATE_FILE"), ("modules.torrents.watchlist", "STATE_FILE"), ("modules.jellyfin.subtitle_fetch", "STATE_FILE"), ("core.maintenance", "STATE_FILE")]:
    module = sys.modules.get(name)
    if module is not None and str(getattr(module, attribute, "")).startswith("/app/"):
        setattr(module, attribute, DATA / Path(getattr(module, attribute)).name)
