import os
from pathlib import Path

# Docker mounts config at /config.json; else use repo-root config.json
# (stable regardless of process cwd — scripts/, poetry, etc.).
_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    "/config.json"
    if os.path.exists("/config.json")
    else str(_REPO_ROOT / "config.json")
)
CONFIG_OBJ = {}
