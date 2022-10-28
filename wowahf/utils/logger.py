import logging
import sys
import os

_log_format = "%(asctime)s - [%(levelname)-7s] - WoW-AHF: %(filename)32s:%(lineno)-3s | %(message)s"

if os.name == 'nt':
    log_path = "wowahf.log"
else:
    log_path = "/var/log/wowahf.log"

loggers = {}

default_level = logging.INFO


def get_stream_handlers(level=logging.INFO):
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(logging.Formatter(_log_format))

    stream_handler_file = logging.FileHandler(log_path, encoding='utf-8')
    stream_handler_file.setLevel(level)
    stream_handler_file.setFormatter(logging.Formatter(_log_format))

    return stream_handler, stream_handler_file


def get_logger(name="WoW-Auc", level=None):
    global default_level
    if level is None:
        level = default_level
    if name in loggers:
        if loggers[name].level == level:
            return loggers[name]
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers = []
    for ahandler in get_stream_handlers(level):
        logger.addHandler(ahandler)
    loggers[name] = logger
    return loggers[name]


def setdebug():
    global default_level
    get_logger().info("Enabling debug logging")
    default_level = logging.DEBUG
    get_logger().debug("Debug logging enabled")


def getdebug():
    return default_level == logging.DEBUG


