"""Recompute missing ah_trans rows for past ok parses when still possible."""

from __future__ import annotations

from datetime import datetime

from psycopg import Connection

from wowahf.db.writes import (
    delete_delta_metrics,
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
from wowahf.pipeline.schedule import delta_metrics_ok, lm_gap_ok
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested

_SENTINEL_METRIC = "market_value_trans"

# Avoid re-scanning huge raw tables every stats chain for the same gap pair.
_attempted: set[tuple[int, int]] = set()


def _can_recompute(
    conn: Connection, parse_id: int
) -> tuple[int, datetime, datetime] | None:
    """Return (prev_id, prev_lm, current_lm) when delta rules allow a recompute."""
    prev = previous_parse_lm(conn, parse_id)
    if prev is None:
        return None
    prev_id, prev_lm = prev
    current_lm = parsed_at_for(conn, parse_id)
    failed = has_completed_failed_parse_between(conn, prev_lm, current_lm)
    if not delta_metrics_ok(prev_lm, current_lm, parse_failed_in_gap=failed):
        return None
    return prev_id, prev_lm, current_lm


def _needs_backfill(
    conn: Connection,
    parse_id: int,
    house_id: int,
    prev_lm: datetime,
    current_lm: datetime,
) -> bool:
    """
    Empty ah_trans needs a pass when deltas were never written, or when a
    large LM jump left placeholder zeros without sale rows.
    """
    if (parse_id, house_id) in _attempted:
        return False
    if load_trans_for_parse(conn, house_id, parse_id):
        return False
    if not metric_exists(conn, parse_id, house_id, _SENTINEL_METRIC):
        return True
    return not lm_gap_ok(prev_lm, current_lm)


def backfill_missing_transactions(conn: Connection) -> int:
    """
    Recompute ah_trans for ok parses that still have previous raw and pass
    the failure-gated delta rules. Clears stale delta metrics when sales are
    recovered so the stats chain can rewrite them.

    Returns the number of (parse, house) pairs recomputed.
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
        prev_id, prev_lm, current_lm = can
        disappeared_at = naive_utc_second(current_lm)

        for house_id in house_ids:
            if stop_requested():
                break
            if not _needs_backfill(
                conn, parse_id, house_id, prev_lm, current_lm
            ):
                continue

            prev_rows = load_raw_for_parse(conn, house_id, prev_id)
            if not prev_rows:
                _attempted.add((parse_id, house_id))
                continue
            curr_rows = load_raw_for_parse(conn, house_id, parse_id)

            watch = load_watch(conn, house_id)
            trans_rows = compute_transactions(
                prev_rows,
                {r[0] for r in curr_rows},
                watch,
                parse_id,
                disappeared_at,
            )
            insert_trans_rows(conn, house_id, trans_rows)
            if trans_rows:
                # Drop placeholder zeros so later jobs refill from real sales.
                delete_delta_metrics(conn, parse_id, house_id)
            _attempted.add((parse_id, house_id))
            filled += 1
            log(
                f"backfill_trans: parse_id={parse_id} house={house_id} "
                f"rows={len(trans_rows)}"
            )

    return filled
