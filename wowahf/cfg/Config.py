from wowahf.utils.logger import get_logger

logger = get_logger()

SESSIONS_VISIBILITY_WEEKS = 4
UNBOOK_BAN_MINUTES = 120


class Config:
    def __init__(self, *args):
        logger.info("Loading {} with parameters {}.".format(self.__class__.__name__, list(args)))
        self.config = {}
        self.valid = False
        pass

    def get(self, path):
        if path in self.config:
            return self.config[path]
        else:
            logger.warning("Config entry {} is not present! Falling back to None".format(path))
            return None

    def set_tmp(self, k, v):
        self.config[k] = v
