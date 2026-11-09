import signal
import threading

_stop = threading.Event()


def request_stop(signum=None, frame=None) -> None:
    _stop.set()


def stop_requested() -> bool:
    return _stop.is_set()


def wait(seconds: float) -> bool:
    """
    Sleep up to `seconds`, returning True if stop was requested.
    """
    if seconds <= 0:
        return stop_requested()
    return _stop.wait(timeout=seconds)


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
