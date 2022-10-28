import os
import json
from wowahf.cfg.Config import Config
from wowahf.utils.logger import get_logger

logger = get_logger()


class JsonConfig(Config):
    def __init__(self, cfg_path=None):
        try:
            super().__init__(cfg_path)
            if cfg_path is None or not cfg_path:
                cfg_path = "config.json"
            self.config = {}
            if os.path.exists(cfg_path):
                self.cfg_path = cfg_path
                with open(self.cfg_path, "r") as config_raw_file:
                    self.config_raw = config_raw_file.read()
                    self.config = json.loads(self.config_raw)
                    self.valid = True
            else:
                self.valid = False
        except Exception:
            self.valid = False
        logger.debug("{}: Loaded config: {}".format(self.__class__.__name__, self.config))
