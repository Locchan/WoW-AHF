import os
from pathlib import Path

# Docker mounts the host data dir at /mounted (config + parse snapshots).
# Locally fall back to the repo-root config.json.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MOUNTED_CONFIG = Path("/mounted/config.json")
CONFIG_PATH = (
    str(_MOUNTED_CONFIG)
    if _MOUNTED_CONFIG.is_file()
    else str(_REPO_ROOT / "config.json")
)
# Directory that holds config.json and durable parse snapshots.
DATA_DIR = Path(CONFIG_PATH).resolve().parent
CONFIG_OBJ = {}
