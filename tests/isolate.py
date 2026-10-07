"""Point the bot's state files at a temporary folder, so a check never writes to /app/data. Import after the project modules."""
import sys, tempfile
from pathlib import Path
DATA = Path(tempfile.mkdtemp(prefix="aegis-data-"))
for name, attribute in [("utils.open_messages", "STATE_FILE"), ("services.torrent_requesters", "STATE_FILE")]:
    module = sys.modules.get(name)
    if module is not None and str(getattr(module, attribute, "")).startswith("/app/"):
        setattr(module, attribute, DATA / Path(getattr(module, attribute)).name)
