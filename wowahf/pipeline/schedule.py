"""Parse due-time helpers (kept free of job/pipeline import cycles)."""

from datetime import UTC, datetime, timedelta

from wowahf.db.connection import connect
from wowahf.db.writes import last_parsed_at
from wowahf.pipeline.snapshot import newest_snapshot_lm
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

    Prev→current LM must be within one hourly dump (+slack). A larger jump
    (missed dumps, downtime, or Blizzard stall) would treat many hours of
    disappearances as a single interval and inflate turnover.
    parse_failed_in_gap is kept for callers/logging; a failure in the gap
    also blocks even if LM somehow looks fine.
    """
    if prev_at is None:
        return False
    if parse_failed_in_gap:
        return False
    return lm_gap_ok(prev_at, current_at)


def _last_known_lm() -> datetime | None:
    """Prefer DB ok parse LM; fall back to newest on-disk snapshot."""
    try:
        conn = connect()
        try:
            last = last_parsed_at(conn)
        finally:
            conn.close()
        if last is not None:
            return naive_utc_second(last)
    except Exception:
        # Job manager pauses schedules on DB outage; disk LM is a silent fallback.
        pass
    disk = newest_snapshot_lm()
    return naive_utc_second(disk) if disk is not None else None


def parse_due_at() -> datetime:
    """
    Next parse = max(last LM + 1h10m, last attempt + 10m).

    Last LM comes from DB when reachable, otherwise from on-disk snapshots so
    downloads keep happening while Postgres is down.
    """
    last = _last_known_lm()
    if last is None:
        due = datetime(1970, 1, 1)
    else:
        due = last + PARSE_INTERVAL
    if _last_parse_attempt is not None:
        due = max(due, _last_parse_attempt + PARSE_RETRY)
    return due
