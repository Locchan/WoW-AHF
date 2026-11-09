import json
import os
from wowahf import utils

def get_config_entry(name):
    if not utils.CONFIG_OBJ:
        if os.path.exists(utils.CONFIG_PATH):
            print("cfg: Loading config from a file...")
            try:
                with open(utils.CONFIG_PATH, 'r') as cfgfile:
                    utils.CONFIG_OBJ = json.load(cfgfile)
            except Exception as e:
                print(f"cfg: Malformed config file: {utils.CONFIG_PATH}")
        else:
            print("cfg: No config file. Using env vars as the config source.")
            utils.CONFIG_OBJ = dict(os.environ)

    if name in utils.CONFIG_OBJ:
        return utils.CONFIG_OBJ[name]
    else:
        print(f"cfg: ERR: There is no entry named '{name}' in the config.")
        exit(1)