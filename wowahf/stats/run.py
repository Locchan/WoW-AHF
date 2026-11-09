from __future__ import annotations

import numpy as np

from wowahf.db.connection import connect
from wowahf.db.migrate import migrate
from wowahf.db.writes import (
    insert_market_prices,
    insert_market_stats,
    list_house_ids,
    list_parse_ids,
    load_raw_for_parse,
    load_trans_for_parse,
    market_price_exists,
    metric_exists,
    previous_parse_id,
)
from wowahf.pipeline.normalize import unit_price
from wowahf.stats.crunch import (
    inflation_rate,
    item_market_features,
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
METRIC_DEPTH = "market_depth"
METRIC_SPREAD = "spread"


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


def _append(
    rows_out: list,
    parse_id: int,
    house_id: int,
    metric: str,
    value: float | int,
    item_id: int | None = None,
) -> None:
    rows_out.append((parse_id, house_id, metric, item_id, value))


def _compute_house_parse(conn, house_id: int, parse_id: int) -> int:
    """Returns number of metric rows written."""
    raw = load_raw_for_parse(conn, house_id, parse_id)
    item_ids, prices, amounts, flags, units = _arrays_from_raw(raw)
    rows_out: list[tuple[int, int, str, int | None, float | int]] = []

    trans = load_trans_for_parse(conn, house_id, parse_id)
    if trans:
        t_items = np.fromiter((r[0] for r in trans), dtype=np.int64, count=len(trans))
        t_prices = np.fromiter((r[1] for r in trans), dtype=np.int64, count=len(trans))
        t_amounts = np.fromiter((r[2] for r in trans), dtype=np.int64, count=len(trans))
        t_flags = np.fromiter((r[3] for r in trans), dtype=np.int64, count=len(trans))
    else:
        t_items = np.array([], dtype=np.int64)
        t_prices = np.array([], dtype=np.int64)
        t_amounts = np.array([], dtype=np.int64)
        t_flags = np.array([], dtype=np.int64)

    need_total = not metric_exists(conn, parse_id, house_id, METRIC_TOTAL)
    need_trans = not metric_exists(conn, parse_id, house_id, METRIC_TRANS)
    need_tvalue = not metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_VALUE)
    need_trate = not metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_RATE)

    total_val: int | None = None
    trans_val: int | None = None

    if need_total or need_trate:
        total_val = market_value_total(prices, amounts, flags, item_ids, units)
    if need_total:
        _append(rows_out, parse_id, house_id, METRIC_TOTAL, total_val)

    if need_trans or need_tvalue or need_trate:
        trans_val = market_value_trans(t_prices, t_amounts, t_flags)
    if need_trans:
        _append(rows_out, parse_id, house_id, METRIC_TRANS, trans_val)
    if need_tvalue:
        _append(rows_out, parse_id, house_id, METRIC_TURNOVER_VALUE, trans_val)

    if not metric_exists(conn, parse_id, house_id, METRIC_TURNOVER_VOLUME):
        _append(
            rows_out,
            parse_id,
            house_id,
            METRIC_TURNOVER_VOLUME,
            turnover_volume(t_amounts),
        )

    if need_trate and total_val is not None and trans_val is not None:
        rate = turnover_rate(trans_val, total_val)
        if rate is not None:
            _append(rows_out, parse_id, house_id, METRIC_TURNOVER_RATE, rate)

    if not metric_exists(conn, parse_id, house_id, METRIC_LISTING_COUNT):
        _append(rows_out, parse_id, house_id, METRIC_LISTING_COUNT, listing_count(len(raw)))

    if not metric_exists(conn, parse_id, house_id, METRIC_VOLUME_TOTAL):
        _append(rows_out, parse_id, house_id, METRIC_VOLUME_TOTAL, volume_total(amounts))

    if not metric_exists(conn, parse_id, house_id, METRIC_UNIQUE_ITEMS):
        _append(rows_out, parse_id, house_id, METRIC_UNIQUE_ITEMS, unique_items(item_ids))

    if not metric_exists(conn, parse_id, house_id, METRIC_SOLD_UNIQUE_ITEMS):
        _append(
            rows_out,
            parse_id,
            house_id,
            METRIC_SOLD_UNIQUE_ITEMS,
            sold_unique_items(t_items),
        )

    need_price = not market_price_exists(conn, parse_id, house_id)
    need_depth = not metric_exists(conn, parse_id, house_id, METRIC_DEPTH)
    need_spread = not metric_exists(conn, parse_id, house_id, METRIC_SPREAD)
    need_inflation = not metric_exists(conn, parse_id, house_id, METRIC_INFLATION)

    feats = None
    if need_price or need_depth or need_spread or need_inflation:
        feats = item_market_features(item_ids, prices, amounts, flags)

    price_rows: list[tuple[int, int, int, int]] = []
    if feats is not None:
        if need_price:
            for item_id, f in feats.items():
                price_rows.append((parse_id, house_id, item_id, f.market_price))
        if need_depth:
            for item_id, f in feats.items():
                _append(rows_out, parse_id, house_id, METRIC_DEPTH, f.depth, item_id)
        if need_spread:
            for item_id, f in feats.items():
                _append(rows_out, parse_id, house_id, METRIC_SPREAD, f.spread_pct, item_id)

    if need_inflation and feats is not None:
        prev_id = previous_parse_id(conn, parse_id)
        if prev_id is not None:
            prev_raw = load_raw_for_parse(conn, house_id, prev_id)
            p_items, p_prices, p_amounts, p_flags, _u = _arrays_from_raw(prev_raw)
            prev_feats = item_market_features(p_items, p_prices, p_amounts, p_flags)
            rate = inflation_rate(
                {i: f.market_price for i, f in feats.items()},
                {i: f.market_price for i, f in prev_feats.items()},
                {i: f.volume for i, f in feats.items()},
            )
            if rate is not None:
                _append(rows_out, parse_id, house_id, METRIC_INFLATION, rate)

    insert_market_stats(conn, rows_out)
    insert_market_prices(conn, price_rows)
    return len(rows_out) + len(price_rows)


def run_stats() -> None:
    """Backfill market_stats for every parse/AH/metric that is still missing."""
    log("stats: started")
    conn = connect()
    try:
        migrate(conn)
        parse_ids = list_parse_ids(conn)
        house_ids = list_house_ids(conn)
        if not parse_ids or not house_ids:
            log("stats: finished (nothing to do)")
            return
        written = 0
        for parse_id in parse_ids:
            if stop_requested():
                log("stats: stop requested, aborting remaining work")
                break
            for house_id in house_ids:
                if stop_requested():
                    break
                written += _compute_house_parse(conn, house_id, parse_id)
        log(
            f"stats: finished parses={len(parse_ids)} "
            f"houses={len(house_ids)} rows_written={written}"
        )
    finally:
        conn.close()
