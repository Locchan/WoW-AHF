from datetime import datetime, timedelta, timezone

from wowahf.db.connection import connect
from wowahf.db.migrate import migrate
from wowahf.db.writes import last_parsed_at
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import run_stats
from wowahf.utils.log import log
from wowahf.utils.shutdown import install_signal_handlers, stop_requested, wait

PARSE_INTERVAL = timedelta(hours=1)
SLEEP_CHUNK_S = 1.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run_after_parse_jobs() -> None:
    """Backfills / post-parse work. Blocks until finished (or stop requested)."""
    if stop_requested():
        return
    try:
        run_stats()
    except Exception as exc:
        log(f"scheduler: stats failed: {exc}")


def _wait_until_due() -> None:
    if stop_requested():
        return

    conn = connect()
    try:
        migrate(conn)
        last = last_parsed_at(conn)
    finally:
        conn.close()

    if stop_requested():
        return

    if last is None:
        log("scheduler: no previous parse, starting now")
        return

    next_at = last + PARSE_INTERVAL
    now = _utc_now()
    wait_s = (next_at - now).total_seconds()
    if wait_s <= 0:
        log(f"scheduler: last parse at {last}, over 1h overdue — starting now")
        return

    log(f"scheduler: last parse at {last}, next at {next_at} (sleeping {wait_s:.0f}s)")
    remaining = wait_s
    while remaining > 0 and not stop_requested():
        chunk = min(SLEEP_CHUNK_S, remaining)
        if wait(chunk):
            break
        remaining -= chunk


if __name__ == "__main__":
    install_signal_handlers()
    log("scheduler: ready (SIGTERM/SIGINT will shut down cleanly)")

    # Finish outstanding backfills before any parse is scheduled.
    log("scheduler: running startup after-parse jobs")
    _run_after_parse_jobs()

    while not stop_requested():
        _wait_until_due()
        if stop_requested():
            break
        try:
            run_parse()
        except Exception as exc:
            log(f"scheduler: parse failed: {exc}")
        if stop_requested():
            break
        _run_after_parse_jobs()

    log("scheduler: shutdown complete")
