import json
import os

from wowahf import utils
from wowahf.utils.log import log


def get_config_entry(name):
    if not utils.CONFIG_OBJ:
        if os.path.exists(utils.CONFIG_PATH):
            try:
                with open(utils.CONFIG_PATH, "r") as cfgfile:
                    utils.CONFIG_OBJ = json.load(cfgfile)
            except Exception:
                log(f"cfg: malformed config file: {utils.CONFIG_PATH}")
                raise
        else:
            utils.CONFIG_OBJ = dict(os.environ)

    if name in utils.CONFIG_OBJ:
        return utils.CONFIG_OBJ[name]

    log(f"cfg: missing config entry '{name}'")
    exit(1)
