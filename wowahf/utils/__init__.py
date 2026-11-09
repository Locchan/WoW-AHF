import os

# Docker mounts config at /config.json; fall back to local ./config.json
CONFIG_PATH = "/config.json" if os.path.exists("/config.json") else "config.json"
CONFIG_OBJ = {}
