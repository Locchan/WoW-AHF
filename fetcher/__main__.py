from fetcher.loop import run_fetcher
from wowahf.utils.log import setup_logging
from wowahf.utils.shutdown import install_signal_handlers


if __name__ == "__main__":
    install_signal_handlers()
    setup_logging()
    run_fetcher()
