from __future__ import annotations

import numpy as np
from psycopg import Connection

from wowahf.db.connection import connect
from wowahf.db.writes import (
    insert_market_depths,
    insert_market_prices,
    insert_market_spreads,
    insert_market_stats,
    insert_trans_candles,
    list_house_ids,
    list_parse_ids,
    load_raw_for_parse,
    load_trans_for_parse,
    market_depth_exists,
    market_price_exists,
    market_spread_exists,
    metric_exists,
    previous_parse_id,
    trans_candle_exists,
)
from wowahf.pipeline.normalize import unit_price
from wowahf.stats.crunch import (
    inflation_rate,
    item_market_features,
    item_trans_candles,
    listing_count,
    market_value_total,
    market_value_trans,
    sold_unique_items,
    turnover_rate,
    turnover_volume,
    unique_items,
    volume_total,
)
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested

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
    rows: list[tuple[int, int, int, int]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not rows:
        empty = np.array([], dtype=np.int64)
        return empty, empty, empty, empty
    n = len(rows)
    item_ids = np.empty(n, dtype=np.int64)
    prices = np.empty(n, dtype=np.int64)
    amounts = np.empty(n, dtype=np.int64)
    flags = np.empty(n, dtype=np.int64)
    for i, (item_id, price, amount, is_commodity) in enumerate(rows):
        item_ids[i] = item_id
        prices[i] = price
        amounts[i] = amount
        flags[i] = int(is_commodity)
    return item_ids, prices, amounts, flags


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
        insert_market_stats(
            conn,
            [(parse_id, house_id, METRIC_LISTING_COUNT, listing_count(len(raw)))],
        )
        return 1

    _run_named("stats_listing_count", work)


def run_volume_total() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_VOLUME_TOTAL):
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        _i, _p, amounts, _f, _u = _arrays_from_raw(raw)
        insert_market_stats(
            conn,
            [(parse_id, house_id, METRIC_VOLUME_TOTAL, volume_total(amounts))],
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


def run_market_value_trans() -> None:
    """Writes market_value_trans and turnover_value (same figure)."""

    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        need_trans = not metric_exists(conn, parse_id, house_id, METRIC_TRANS)
        need_tvalue = not metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_VALUE)
        if not need_trans and not need_tvalue:
            return 0
        t_items, t_prices, t_amounts, t_flags = _arrays_from_trans(
            load_trans_for_parse(conn, house_id, parse_id)
        )
        value = market_value_trans(t_prices, t_amounts, t_flags, t_items)
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
        _i, _p, t_amounts, _f = _arrays_from_trans(
            load_trans_for_parse(conn, house_id, parse_id)
        )
        insert_market_stats(
            conn,
            [
                (
                    parse_id,
                    house_id,
                    METRIC_TURNOVER_VOLUME,
                    turnover_volume(t_amounts),
                )
            ],
        )
        return 1

    _run_named("stats_turnover_volume", work)


def run_sold_unique_items() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_SOLD_UNIQUE_ITEMS):
            return 0
        t_items, _p, _a, _f = _arrays_from_trans(
            load_trans_for_parse(conn, house_id, parse_id)
        )
        insert_market_stats(
            conn,
            [
                (
                    parse_id,
                    house_id,
                    METRIC_SOLD_UNIQUE_ITEMS,
                    sold_unique_items(t_items),
                )
            ],
        )
        return 1

    _run_named("stats_sold_unique_items", work)


def run_turnover_rate() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_RATE):
            return 0
        total = _metric_value(conn, parse_id, house_id, METRIC_TOTAL)
        trans = _metric_value(conn, parse_id, house_id, METRIC_TRANS)
        if total is None or trans is None:
            return 0
        rate = turnover_rate(int(trans), int(total))
        if rate is None:
            return 0
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


def run_inflation_rate() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if metric_exists(conn, parse_id, house_id, METRIC_INFLATION):
            return 0
        prev_id = previous_parse_id(conn, parse_id)
        if prev_id is None:
            return 0
        raw = load_raw_for_parse(conn, house_id, parse_id)
        item_ids, prices, amounts, flags, _u = _arrays_from_raw(raw)
        feats = item_market_features(item_ids, prices, amounts, flags)
        prev_raw = load_raw_for_parse(conn, house_id, prev_id)
        p_items, p_prices, p_amounts, p_flags, _pu = _arrays_from_raw(prev_raw)
        prev_feats = item_market_features(p_items, p_prices, p_amounts, p_flags)
        rate = inflation_rate(
            {i: f.market_price for i, f in feats.items()},
            {i: f.market_price for i, f in prev_feats.items()},
            {i: f.volume for i, f in feats.items()},
        )
        if rate is None:
            return 0
        insert_market_stats(
            conn, [(parse_id, house_id, METRIC_INFLATION, rate)]
        )
        return 1

    _run_named("stats_inflation_rate", work)


def run_trans_candle() -> None:
    def work(conn: Connection, house_id: int, parse_id: int) -> int:
        if trans_candle_exists(conn, parse_id, house_id):
            return 0
        t_items, t_prices, t_amounts, t_flags = _arrays_from_trans(
            load_trans_for_parse(conn, house_id, parse_id)
        )
        if not len(t_items):
            return 0
        rows = [
            (
                parse_id,
                house_id,
                item_id,
                c.high,
                c.low,
                c.volume,
                c.turnover,
            )
            for item_id, c in item_trans_candles(
                t_items, t_prices, t_amounts, t_flags
            ).items()
        ]
        insert_trans_candles(conn, rows)
        return len(rows)

    _run_named("stats_trans_candle", work)


# Ordered chain after parse / on startup.
STATS_JOBS: list[tuple[str, object]] = [
    ("stats_market_value_total", run_market_value_total),
    ("stats_listing_count", run_listing_count),
    ("stats_volume_total", run_volume_total),
    ("stats_unique_items", run_unique_items),
    ("stats_market_value_trans", run_market_value_trans),
    ("stats_turnover_volume", run_turnover_volume),
    ("stats_sold_unique_items", run_sold_unique_items),
    ("stats_turnover_rate", run_turnover_rate),
    ("stats_market_price", run_market_price),
    ("stats_market_depth", run_market_depth),
    ("stats_market_spread", run_market_spread),
    ("stats_inflation_rate", run_inflation_rate),
    ("stats_trans_candle", run_trans_candle),
]
