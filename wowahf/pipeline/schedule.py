"""Parse due-time helpers (kept free of job/pipeline import cycles)."""

from datetime import UTC, datetime, timedelta

from wowahf.db.connection import connect
from wowahf.db.writes import last_parsed_at
from wowahf.utils.http_date import naive_utc_second

# Blizzard AH dumps refresh hourly; wait a bit past LM so the new
# snapshot is published before we fetch.
PARSE_INTERVAL = timedelta(hours=1, minutes=10)
# If LM has not advanced yet, retry on this cadence instead of spinning.
PARSE_RETRY = timedelta(minutes=10)

_last_parse_attempt: datetime | None = None


def mark_parse_attempt() -> None:
    """Record wall-clock attempt so unchanged-LM skips back off."""
    global _last_parse_attempt
    _last_parse_attempt = naive_utc_second(datetime.now(UTC))


def parse_due_at() -> datetime:
    """
    Next parse = max(last LM + 1h10m, last attempt + 10m).

    The attempt floor stops a tight loop when Blizzard still serves the
    previous Last-Modified after the LM+1h10m due time.
    """
    conn = connect()
    try:
        last = last_parsed_at(conn)
    finally:
        conn.close()
    if last is None:
        due = datetime(1970, 1, 1)
    else:
        due = naive_utc_second(last) + PARSE_INTERVAL
    if _last_parse_attempt is not None:
        due = max(due, _last_parse_attempt + PARSE_RETRY)
    return due
