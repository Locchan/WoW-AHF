"""Recompute missing ah_trans rows for past ok parses when still possible."""

from __future__ import annotations

from datetime import datetime

from psycopg import Connection

from wowahf.db.writes import (
    has_completed_failed_parse_between,
    insert_trans_rows,
    list_house_ids,
    list_parse_ids,
    load_raw_for_parse,
    load_trans_for_parse,
    load_watch,
    metric_exists,
    parsed_at_for,
    previous_parse_lm,
)
from wowahf.pipeline.crunch import compute_transactions
from wowahf.pipeline.schedule import delta_metrics_ok
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested

# Presence of this metric means the delta path already finalized (incl. quiet 0).
_SENTINEL_METRIC = "market_value_trans"


def _can_recompute(
    conn: Connection, parse_id: int
) -> tuple[int, datetime] | None:
    """Return (prev_id, current_lm) when delta rules allow a recompute."""
    prev = previous_parse_lm(conn, parse_id)
    if prev is None:
        return None
    prev_id, prev_lm = prev
    current_lm = parsed_at_for(conn, parse_id)
    failed = has_completed_failed_parse_between(conn, prev_lm, current_lm)
    if not delta_metrics_ok(prev_lm, current_lm, parse_failed_in_gap=failed):
        return None
    return prev_id, current_lm


def backfill_missing_transactions(conn: Connection) -> int:
    """
    For each ok parse/house with no ah_trans and no market_value_trans yet,
    recompute sales from retained raw snapshots when the failure-gated delta
    rules allow it and the previous parse's raw is still present.

    Returns the number of (parse, house) pairs that received a recompute pass
    (including quiet hours that stay empty).
    """
    parse_ids = list_parse_ids(conn)
    house_ids = list_house_ids(conn)
    if not parse_ids or not house_ids:
        return 0

    filled = 0
    for parse_id in parse_ids:
        if stop_requested():
            break
        can = _can_recompute(conn, parse_id)
        if can is None:
            continue
        prev_id, current_lm = can
        disappeared_at = naive_utc_second(current_lm)

        for house_id in house_ids:
            if stop_requested():
                break
            if metric_exists(conn, parse_id, house_id, _SENTINEL_METRIC):
                continue
            if load_trans_for_parse(conn, house_id, parse_id):
                continue

            prev_rows = load_raw_for_parse(conn, house_id, prev_id)
            if not prev_rows:
                # Previous raw purged — cannot recalculate.
                continue
            curr_rows = load_raw_for_parse(conn, house_id, parse_id)
            # Current raw may be empty (no listings); still valid for sales.

            watch = load_watch(conn, house_id)
            trans_rows = compute_transactions(
                prev_rows,
                {r[0] for r in curr_rows},
                watch,
                parse_id,
                disappeared_at,
            )
            insert_trans_rows(conn, house_id, trans_rows)
            filled += 1
            if trans_rows:
                log(
                    f"backfill_trans: parse_id={parse_id} house={house_id} "
                    f"rows={len(trans_rows)}"
                )

    return filled
