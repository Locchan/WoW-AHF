from __future__ import annotations

import numpy as np
from psycopg import Connection

from wowahf.db.connection import connect
from wowahf.db.writes import (
    has_unrecoverable_parse_gap,
    insert_market_depths,
    insert_market_prices,
    insert_market_spreads,
    insert_market_stats,
    insert_item_median_deltas,
    insert_item_transaction_stats,
    item_median_delta_exists,
    list_house_ids,
    list_parse_ids,
    load_item_tx_medians,
    load_market_depths,
    load_market_prices,
    load_raw_for_parse,
    load_trans_for_parse,
    load_tx_weighted_avg_medians,
    market_depth_exists,
    market_price_exists,
    market_spread_exists,
    metric_exists,
    parsed_at_for,
    previous_parse_lm,
    item_transaction_stats_exists,
)
from wowahf.pipeline.backfill_trans import backfill_missing_transactions
from wowahf.pipeline.normalize import unit_price
from wowahf.pipeline.schedule import delta_metrics_ok
from wowahf.stats.crunch import (
    inflation_rate,
    item_market_features,
    item_transaction_stats,
    listing_count_filtered,
    market_value_total,
    market_value_trans,
    median_delta_pct,
    sold_unique_items,
    turnover_rate,
    turnover_volume,
    unique_items,
    volume_total,
)
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def _delta_prev_ok(conn: Connection, parse_id: int) -> bool:
    """True when deltas are allowed vs the previous ok parse."""
    prev = previous_parse_lm(conn, parse_id)
    if prev is None:
        return False
    current_lm = parsed_at_for(conn, parse_id)
    failed = has_unrecoverable_parse_gap(conn, prev[0], parse_id)
    return delta_metrics_ok(
        prev[1], current_lm, parse_failed_in_gap=failed
    )

METRIC_TOTAL = "market_value_total"
METRIC_TRANS = "market_value_trans"
METRIC_TURNOVER_VALUE = "turnover_value"
METRIC_TURNOVER_VOLUME = "turnover_volume"
METRIC_TURNOVER_RATE = "turnover_rate"
METRIC_LISTING_COUNT = "listing_count"
METRIC_VOLUME_TOTAL = "volume_total"
METRIC_UNIQUE_ITEMS = "unique_items"
METRIC_SOLD_UNIQUE_ITEMS = "sold_unique_items"
METRIC_INFLATION = "inflation_rate"


def _arrays_from_raw(
    rows: list[tuple[int, int, int, int, str, int]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(rows)
    item_ids = np.empty(n, dtype=np.int64)
    prices = np.empty(n, dtype=np.int64)
    amounts = np.empty(n, dtype=np.int64)
    flags = np.empty(n, dtype=np.int64)
    units = np.empty(n, dtype=np.int64)
    for i, (_auc, item_id, price, amount, _tl, is_commodity) in enumerate(rows):
        item_ids[i] = item_id
        prices[i] = price
        amounts[i] = amount
        flags[i] = int(is_commodity)
        units[i] = unit_price(price, amount, bool(is_commodity))
    return item_ids, prices, amounts, flags, units


def _arrays_from_trans(
    rows: list[tuple[int, int, int, int, int]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not rows:
        empty = np.array([], dtype=np.int64)
        return empty, empty, empty, empty, empty
    n = len(rows)
    item_ids = np.empty(n, dtype=np.int64)
    prices = np.empty(n, dtype=np.int64)
    amounts = np.empty(n, dtype=np.int64)
    flags = np.empty(n, dtype=np.int64)
    lots = np.empty(n, dtype=np.int64)
    for i, (item_id, price, amount, is_commodity, lot_n) in enumerate(rows):
        item_ids[i] = item_id
        prices[i] = price
        amounts[i] = amount
        flags[i] = int(is_commodity)
        lots[i] = int(lot_n)
    return item_ids, prices, amounts, flags, lots


def _metric_value(
    conn: Connection, parse_id: int, ah_id: int, metric: str
) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT value FROM market_stats
            WHERE parse_id = %s AND ah_id = %s AND metric = %s
            LIMIT 1
            """,
            (parse_id, ah_id, metric),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def _run_named(name: str, work) -> None:
    """Backfill ``work(conn, house_id, parse_id) -> rows_written`` for all pairs."""
    log(f"{name}: started")
    conn = connect()
    try:
        parse_ids = list_parse_ids(conn)
        house_ids = list_house_ids(conn)
        if not parse_ids or not house_ids:
            log(f"{name}: finished (nothing to do)")
            return
        written = 0
        for parse_id in parse_ids:
            if stop_requested():
                log(f"{name}: stop requested, aborting remaining work")
                break
            for house_id in house_ids:
                if stop_requested():
                    break
                written += work(conn, house_id, parse_id)
        log(
            f"{name}: finished parses={len(parse_ids)} "
            f"houses={len(house_ids)} rows_written={written}"
        )
    finally:
        conn.close()


def run_market_value_total() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_TOTAL):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, prices, amounts, flags, units = _arrays_from_raw(raw)
        value = market_value_total(prices, amounts, flags, item_ids, units)
        insert_market_stats(conn, [(parse_id, house_id, METRIC_TOTAL, value)])
        return 1

    _run_named("stats_market_value_total", work)


def run_listing_count() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_LISTING_COUNT):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, _p, _a, _f, _u = _arrays_from_raw(raw)
        insert_market_stats(
            conn,
            [
                (
                    parse_id,
                    house_id,
                    METRIC_LISTING_COUNT,
                    listing_count_filtered(item_ids),
                )
            ],
        )
        return 1

    _run_named("stats_listing_count", work)


def run_volume_total() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_VOLUME_TOTAL):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, _p, amounts, _f, _u = _arrays_from_raw(raw)
        insert_market_stats(
            conn,
            [
                (
                    parse_id,
                    house_id,
                    METRIC_VOLUME_TOTAL,
                    volume_total(item_ids, amounts),
                )
            ],
        )
        return 1

    _run_named("stats_volume_total", work)


def run_unique_items() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_UNIQUE_ITEMS):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, _p, _a, _f, _u = _arrays_from_raw(raw)
        insert_market_stats(
            conn,
            [(parse_id, house_id, METRIC_UNIQUE_ITEMS, unique_items(item_ids))],
        )
        return 1

    _run_named("stats_unique_items", work)


def run_backfill_transactions() -> None:
    """Recompute missing ah_trans for past ok parses when raw is still available."""
    log("stats_backfill_trans: started")
    conn = connect()
    try:
        n = backfill_missing_transactions(conn)
        log(f"stats_backfill_trans: finished pairs_recomputed={n}")
    finally:
        conn.close()


def run_market_value_trans() -> None:
    """Writes market_value_trans and turnover_value (same figure)."""

    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        need_trans = not metric_exists(conn, parse_id, house_id, METRIC_TRANS)
        need_tvalue = not metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_VALUE)
        if not need_trans and not need_tvalue:
            return 0
        # Multi-dump gaps: write 0 so Grafana stays continuous (not real sales).
        if not _delta_prev_ok(conn, parse_id):
            value = 0
        else:
            t_items, t_prices, t_amounts, t_flags, t_lots = _arrays_from_trans(
                load_trans_for_parse(conn, house_id, parse_id)
            )
            value = market_value_trans(
                t_prices, t_amounts, t_flags, t_items, t_lots
            )
        rows: list[tuple[int, int, str, float | int]] = []
        if need_trans:
            rows.append((parse_id, house_id, METRIC_TRANS, value))
        if need_tvalue:
            rows.append((parse_id, house_id, METRIC_TURNOVER_VALUE, value))
        insert_market_stats(conn, rows)
        return len(rows)

    _run_named("stats_market_value_trans", work)


def run_turnover_volume() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_VOLUME):
            return 0
        if not _delta_prev_ok(conn, parse_id):
            value = 0
        else:
            t_items, _p, t_amounts, _f, t_lots = _arrays_from_trans(
                load_trans_for_parse(conn, house_id, parse_id)
            )
            value = turnover_volume(t_items, t_amounts, t_lots)
        insert_market_stats(
            conn,
            [(parse_id, house_id, METRIC_TURNOVER_VOLUME, value)],
        )
        return 1

    _run_named("stats_turnover_volume", work)


def run_sold_unique_items() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_SOLD_UNIQUE_ITEMS):
            return 0
        if not _delta_prev_ok(conn, parse_id):
            value = 0
        else:
            t_items, _p, _a, _f, t_lots = _arrays_from_trans(
                load_trans_for_parse(conn, house_id, parse_id)
            )
            value = sold_unique_items(t_items, t_lots)
        insert_market_stats(
            conn,
            [(parse_id, house_id, METRIC_SOLD_UNIQUE_ITEMS, value)],
        )
        return 1

    _run_named("stats_sold_unique_items", work)


def run_turnover_rate() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_RATE):
            return 0
        if not _delta_prev_ok(conn, parse_id):
            rate = 0.0
        else:
            total = _metric_value(conn, parse_id, house_id, METRIC_TOTAL)
            trans = _metric_value(conn, parse_id, house_id, METRIC_TRANS)
            if total is None or trans is None:
                return 0
            rate = turnover_rate(int(trans), int(total))
            if rate is None:
                rate = 0.0
        insert_market_stats(
            conn, [(parse_id, house_id, METRIC_TURNOVER_RATE, rate)]
        )
        return 1

    _run_named("stats_turnover_rate", work)


def run_market_price() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if market_price_exists(conn, parse_id, house_id):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, prices, amounts, flags, _u = _arrays_from_raw(raw)
        feats = item_market_features(item_ids, prices, amounts, flags)
        rows = [
            (parse_id, house_id, item_id, f.market_price)
            for item_id, f in feats.items()
        ]
        insert_market_prices(conn, rows)
        return len(rows)

    _run_named("stats_market_price", work)


def run_market_depth() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if market_depth_exists(conn, parse_id, house_id):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, prices, amounts, flags, _u = _arrays_from_raw(raw)
        feats = item_market_features(item_ids, prices, amounts, flags)
        rows = [
            (parse_id, house_id, item_id, f.depth) for item_id, f in feats.items()
        ]
        insert_market_depths(conn, rows)
        return len(rows)

    _run_named("stats_market_depth", work)


def run_market_spread() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if market_spread_exists(conn, parse_id, house_id):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, prices, amounts, flags, _u = _arrays_from_raw(raw)
        feats = item_market_features(item_ids, prices, amounts, flags)
        rows = [
            (parse_id, house_id, item_id, f.spread_pct)
            for item_id, f in feats.items()
        ]
        insert_market_spreads(conn, rows)
        return len(rows)

    _run_named("stats_market_spread", work)


def _inflation_inputs(
    conn: Connection, house_id: int, parse_id: int
) -> tuple[dict[int, int], dict[int, int]]:
    """
    Prices + near-ask depths for inflation.

    Prefer persisted market_price / market_depth so history beyond the raw
    retention window can be rebuilt. Fall back to raw features when tables
    are empty for this parse.
    """
    prices = load_market_prices(conn, house_id, parse_id)
    depths = load_market_depths(conn, house_id, parse_id)
    if prices and depths:
        return prices, depths
    raw = load_raw_for_parse(conn, house_id, parse_id)
    if not raw:
        return {}, {}
    item_ids, p, amounts, flags, _u = _arrays_from_raw(raw)
    feats = item_market_features(item_ids, p, amounts, flags)
    return (
        {i: f.market_price for i, f in feats.items()},
        {i: f.depth for i, f in feats.items()},
    )


def run_inflation_rate() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_INFLATION):
            return 0
        prev = previous_parse_lm(conn, parse_id)
        if prev is None:
            return 0
        prev_id, prev_lm = prev
        current_lm = parsed_at_for(conn, parse_id)
        failed = has_unrecoverable_parse_gap(conn, prev_id, parse_id)
        if not delta_metrics_ok(
            prev_lm, current_lm, parse_failed_in_gap=failed
        ):
            insert_market_stats(
                conn, [(parse_id, house_id, METRIC_INFLATION, 0.0)]
            )
            return 1
        cur_prices, cur_depths = _inflation_inputs(conn, house_id, parse_id)
        prev_prices, _prev_depths = _inflation_inputs(conn, house_id, prev_id)
        if not cur_prices or not prev_prices:
            return 0
        rate = inflation_rate(cur_prices, prev_prices, cur_depths)
        # No liquid basket → 0 so series stay continuous in Grafana.
        if rate is None:
            rate = 0.0
        insert_market_stats(
            conn, [(parse_id, house_id, METRIC_INFLATION, rate)]
        )
        return 1

    _run_named("stats_inflation_rate", work)


def run_item_transaction_stats() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if item_transaction_stats_exists(conn, parse_id, house_id):
            return 0
        if not _delta_prev_ok(conn, parse_id):
            return 0
        t_items, t_prices, t_amounts, t_flags, t_lots = _arrays_from_trans(
            load_trans_for_parse(conn, house_id, parse_id)
        )
        if not len(t_items):
            return 0
        rows = [
            (
                parse_id,
                house_id,
                item_id,
                s.low,
                s.high,
                s.median,
                s.tx_count,
            )
            for item_id, s in item_transaction_stats(
                t_items, t_prices, t_amounts, t_flags, t_lots
            ).items()
        ]
        insert_item_transaction_stats(conn, rows)
        return len(rows)

    _run_named("stats_item_transaction", work)


def run_item_median_delta() -> None:
    """Backfill pct vs tx-weighted 5d median for every parse/house."""

    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if item_median_delta_exists(conn, parse_id, house_id):
            return 0
        current = load_item_tx_medians(conn, house_id, parse_id)
        if not current:
            return 0
        parsed_at = parsed_at_for(conn, parse_id)
        avg5d = load_tx_weighted_avg_medians(
            conn, house_id, list(current.keys()), parsed_at
        )
        rows: list[tuple[int, int, int, float]] = []
        for item_id, median in current.items():
            base = avg5d.get(item_id)
            if base is None:
                continue
            pct = median_delta_pct(median, base)
            if pct is None:
                continue
            rows.append((parse_id, house_id, item_id, pct))
        if not rows:
            return 0
        insert_item_median_deltas(conn, rows)
        return len(rows)

    _run_named("stats_item_median_delta", work)


# Ordered chain after parse / on startup.
STATS_JOBS: list[tuple[str, object]] = [
    ("stats_market_value_total", run_market_value_total),
    ("stats_listing_count", run_listing_count),
    ("stats_volume_total", run_volume_total),
    ("stats_unique_items", run_unique_items),
    ("stats_backfill_trans", run_backfill_transactions),
    ("stats_market_value_trans", run_market_value_trans),
    ("stats_turnover_volume", run_turnover_volume),
    ("stats_sold_unique_items", run_sold_unique_items),
    ("stats_turnover_rate", run_turnover_rate),
    ("stats_market_price", run_market_price),
    ("stats_market_depth", run_market_depth),
    ("stats_market_spread", run_market_spread),
    ("stats_inflation_rate", run_inflation_rate),
    ("stats_item_transaction", run_item_transaction_stats),
    ("stats_item_median_delta", run_item_median_delta),
]
