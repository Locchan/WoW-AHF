"""Shared AH schedule constants and sale-delta helpers."""

from datetime import datetime, timedelta

from wowahf.utils.http_date import naive_utc_second

# Blizzard AH dumps refresh hourly; wait a bit past LM so the new
# snapshot is published before we fetch (fetcher container).
PARSE_INTERVAL = timedelta(hours=1, minutes=10)
# If LM has not advanced yet, fetcher retries on this cadence.
PARSE_RETRY = timedelta(minutes=10)
# Sale inference and inflation assume ~one hourly dump between ok parses.
# Larger LM gaps (missed dumps from 401s etc.) must not count as one interval.
PARSE_DELTA_MAX = timedelta(hours=1, minutes=30)


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
