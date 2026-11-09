import time
from datetime import datetime, timedelta, timezone

from wowahf.db.connection import connect
from wowahf.db.migrate import migrate
from wowahf.db.writes import last_parsed_at
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import run_stats
from wowahf.utils.log import log

PARSE_INTERVAL = timedelta(hours=1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _wait_until_due() -> None:
    conn = connect()
    try:
        migrate(conn)
        last = last_parsed_at(conn)
    finally:
        conn.close()

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
    time.sleep(wait_s)


if __name__ == "__main__":
    while True:
        _wait_until_due()
        try:
            run_parse()
        except Exception as exc:
            log(f"scheduler: parse failed: {exc}")
        try:
            run_stats()
        except Exception as exc:
            log(f"scheduler: stats failed: {exc}")
