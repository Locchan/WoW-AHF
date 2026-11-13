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
# Sale inference and inflation assume ~one hourly dump between ok parses.
# Larger LM gaps (missed dumps from 401s etc.) must not count as one interval.
PARSE_DELTA_MAX = timedelta(hours=1, minutes=30)

_last_parse_attempt: datetime | None = None


def mark_parse_attempt() -> None:
    """Record wall-clock attempt so unchanged-LM skips back off."""
    global _last_parse_attempt
    _last_parse_attempt = naive_utc_second(datetime.now(UTC))


def lm_gap_ok(prev_at: datetime | None, current_at: datetime) -> bool:
    """True when prev→current Last-Modified is within one dump (+slack)."""
    if prev_at is None:
        return False
    gap = naive_utc_second(current_at) - naive_utc_second(prev_at)
    return timedelta(0) < gap <= PARSE_DELTA_MAX


def delta_metrics_ok(
    prev_at: datetime | None,
    current_at: datetime,
    *,
    parse_failed_in_gap: bool,
) -> bool:
    """
    Allow sale inference / turnover / inflation for this step.

    A large LM jump alone is fine (Blizzard delayed the next dump while we
    only saw unchanged skips). Block only when a parse job actually failed
    in between — that is when we may have missed a published dump.
    """
    if prev_at is None:
        return False
    if lm_gap_ok(prev_at, current_at):
        return True
    return not parse_failed_in_gap


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
