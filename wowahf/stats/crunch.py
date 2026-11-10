"""Market-stat calculations (NumPy). No DB I/O here."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from wowahf.pipeline.normalize import listing_value, unit_price

PRICE_JUMP_RATIO = 1.10  # >10% gap between neighbour price levels
DEPTH_PRICE_FACTOR = 1.05  # depth = volume at <= market_price * this
INFLATION_VOLUME_MIN = 3000
INFLATION_CHANGE_JUMP = 10.0  # percentage-points gap when culling % changes


@dataclass(frozen=True)
class ItemMarketFeatures:
    market_price: int
    depth: int  # units at <= market_price * DEPTH_PRICE_FACTOR
    spread_pct: float  # (high-low)/market_price * 100 within main cluster
    volume: int


def _price_levels(
    unit_prices: np.ndarray, volumes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Collapse to unique unit prices with summed volume, sorted ascending."""
    if len(unit_prices) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    order = np.argsort(unit_prices)
    prices = unit_prices[order]
    vols = volumes[order]
    if len(prices) == 1:
        return prices.astype(np.int64), vols.astype(np.int64)
    changed = prices[1:] != prices[:-1]
    starts = np.concatenate(([0], np.flatnonzero(changed) + 1))
    out_p = prices[starts]
    out_v = np.add.reduceat(vols, starts)
    return out_p.astype(np.int64), out_v.astype(np.int64)


def _clusters(prices: np.ndarray) -> list[tuple[int, int]]:
    n = len(prices)
    if n == 0:
        return []
    bounds = [0]
    for i in range(n - 1):
        if prices[i] <= 0:
            bounds.append(i + 1)
            continue
        if prices[i + 1] > prices[i] * PRICE_JUMP_RATIO:
            bounds.append(i + 1)
    bounds.append(n)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _main_cluster_range(
    prices: np.ndarray, volumes: np.ndarray
) -> tuple[int, int] | None:
    clusters = _clusters(prices)
    if not clusters:
        return None
    return max(clusters, key=lambda se: int(volumes[se[0] : se[1]].sum()))


def market_value_total(
    prices: np.ndarray,
    amounts: np.ndarray,
    is_commodity: np.ndarray,
    item_ids: np.ndarray,
    unit_prices: np.ndarray,
) -> int:
    """Buy-out cost excluding per-item top outlier clusters."""
    if len(prices) == 0:
        return 0

    total = 0
    order = np.argsort(item_ids)
    item_ids = item_ids[order]
    prices = prices[order]
    amounts = amounts[order]
    is_commodity = is_commodity[order]
    unit_prices = unit_prices[order]

    if len(item_ids) > 1:
        cuts = np.flatnonzero(item_ids[1:] != item_ids[:-1]) + 1
    else:
        cuts = np.array([], dtype=np.int64)
    starts = np.concatenate((np.array([0], dtype=np.int64), cuts))
    ends = np.concatenate((cuts, np.array([len(item_ids)], dtype=np.int64)))

    for start, end in zip(starts.tolist(), ends.tolist()):
        lvl_p, lvl_v = _price_levels(unit_prices[start:end], amounts[start:end])
        main = _main_cluster_range(lvl_p, lvl_v)
        if main is None:
            continue
        max_keep = int(lvl_p[main[1] - 1])
        for i in range(start, end):
            if int(unit_prices[i]) <= max_keep:
                total += listing_value(
                    int(prices[i]), int(amounts[i]), bool(is_commodity[i])
                )
    return int(total)


def market_value_trans(
    prices: np.ndarray,
    amounts: np.ndarray,
    is_commodity: np.ndarray,
    item_ids: np.ndarray,
) -> int:
    """
    Inferred sales value with the same per-item outlier-cluster cull as
    market_value_total (junk commodity unit prices must not dominate turnover).
    """
    if len(prices) == 0:
        return 0
    units = np.empty(len(prices), dtype=np.int64)
    for i in range(len(prices)):
        units[i] = unit_price(
            int(prices[i]), int(amounts[i]), bool(is_commodity[i])
        )
    return market_value_total(prices, amounts, is_commodity, item_ids, units)


@dataclass(frozen=True)
class ItemTransactionStats:
    """Culled sold prices for one item in one parse."""

    low: int
    high: int
    median: int  # lots-weighted median unit copper
    tx_count: int  # sold listing count


def _lots_weighted_median(unit_prices: list[int], lot_counts: list[int]) -> int:
    """Median unit price where each sold listing counts once."""
    pairs = sorted(zip(unit_prices, lot_counts), key=lambda pl: pl[0])
    total = sum(lot_counts)
    if total <= 0:
        return 0
    # For even N, lower of the two central values (integer copper).
    target = (total + 1) // 2
    seen = 0
    for price, n in pairs:
        seen += int(n)
        if seen >= target:
            return int(price)
    return int(pairs[-1][0])


def item_transaction_stats(
    item_ids: np.ndarray,
    prices: np.ndarray,
    amounts: np.ndarray,
    is_commodity: np.ndarray,
    lots: np.ndarray,
) -> dict[int, ItemTransactionStats]:
    """
    Per-item low/high/median + sold-listing count from inferred sales,
    keeping only lots in the main volume cluster (same cull as
    market_value_total). All-outlier items produce no row.
    """
    if len(item_ids) == 0:
        return {}

    units_arr = np.empty(len(item_ids), dtype=np.int64)
    for i in range(len(item_ids)):
        units_arr[i] = unit_price(
            int(prices[i]), int(amounts[i]), bool(is_commodity[i])
        )

    order = np.argsort(item_ids)
    item_ids = item_ids[order]
    amounts = amounts[order]
    lots = lots[order]
    units_arr = units_arr[order]

    if len(item_ids) > 1:
        cuts = np.flatnonzero(item_ids[1:] != item_ids[:-1]) + 1
    else:
        cuts = np.array([], dtype=np.int64)
    starts = np.concatenate((np.array([0], dtype=np.int64), cuts))
    ends = np.concatenate((cuts, np.array([len(item_ids)], dtype=np.int64)))

    out: dict[int, ItemTransactionStats] = {}
    for start, end in zip(starts.tolist(), ends.tolist()):
        pos = [i for i in range(start, end) if int(units_arr[i]) > 0]
        if not pos:
            continue
        pos_units = np.fromiter((units_arr[i] for i in pos), dtype=np.int64)
        pos_amts = np.fromiter((amounts[i] for i in pos), dtype=np.int64)
        lvl_p, lvl_v = _price_levels(pos_units, pos_amts)
        main = _main_cluster_range(lvl_p, lvl_v)
        if main is None:
            continue
        max_keep = int(lvl_p[main[1] - 1])
        kept_prices: list[int] = []
        kept_lots: list[int] = []
        for i in pos:
            u = int(units_arr[i])
            if u > max_keep:
                continue
            kept_prices.append(u)
            kept_lots.append(int(lots[i]))
        if not kept_prices:
            continue
        out[int(item_ids[start])] = ItemTransactionStats(
            low=min(kept_prices),
            high=max(kept_prices),
            median=_lots_weighted_median(kept_prices, kept_lots),
            tx_count=sum(kept_lots),
        )
    return out


def listing_count(n_rows: int) -> int:
    return int(n_rows)


def volume_total(amounts: np.ndarray) -> int:
    return int(amounts.sum()) if len(amounts) else 0


def unique_items(item_ids: np.ndarray) -> int:
    return int(np.unique(item_ids).size) if len(item_ids) else 0


def item_market_features(
    item_ids: np.ndarray,
    prices: np.ndarray,
    amounts: np.ndarray,
    is_commodity: np.ndarray,
) -> dict[int, ItemMarketFeatures]:
    """Per-item market_price, depth, spread_pct, volume."""
    if len(item_ids) == 0:
        return {}

    unit_prices = np.empty(len(item_ids), dtype=np.int64)
    for i in range(len(item_ids)):
        unit_prices[i] = unit_price(
            int(prices[i]), int(amounts[i]), bool(is_commodity[i])
        )

    order = np.argsort(item_ids)
    item_ids = item_ids[order]
    unit_prices = unit_prices[order]
    amounts = amounts[order]

    out: dict[int, ItemMarketFeatures] = {}
    if len(item_ids) > 1:
        cuts = np.flatnonzero(item_ids[1:] != item_ids[:-1]) + 1
    else:
        cuts = np.array([], dtype=np.int64)
    starts = np.concatenate((np.array([0], dtype=np.int64), cuts))
    ends = np.concatenate((cuts, np.array([len(item_ids)], dtype=np.int64)))

    for start, end in zip(starts.tolist(), ends.tolist()):
        up = unit_prices[start:end]
        vol = amounts[start:end]
        lvl_p, lvl_v = _price_levels(up, vol)
        main = _main_cluster_range(lvl_p, lvl_v)
        if main is None:
            continue
        ms, me = main
        cluster_prices = lvl_p[ms:me]
        market_price = int(cluster_prices.min())
        high = int(cluster_prices.max())
        low = market_price
        spread_pct = 0.0 if market_price <= 0 else (high - low) * 100.0 / market_price
        depth_cap = market_price * DEPTH_PRICE_FACTOR
        depth = int(vol[up <= depth_cap].sum())
        out[int(item_ids[start])] = ItemMarketFeatures(
            market_price=market_price,
            depth=depth,
            spread_pct=float(np.round(spread_pct, 2)),
            volume=int(vol.sum()),
        )
    return out


def market_prices_by_item(
    item_ids: np.ndarray,
    prices: np.ndarray,
    amounts: np.ndarray,
    is_commodity: np.ndarray,
) -> dict[int, int]:
    feats = item_market_features(item_ids, prices, amounts, is_commodity)
    return {iid: f.market_price for iid, f in feats.items()}


def item_volumes(
    item_ids: np.ndarray, amounts: np.ndarray
) -> dict[int, int]:
    if len(item_ids) == 0:
        return {}
    order = np.argsort(item_ids)
    item_ids = item_ids[order]
    amounts = amounts[order]
    if len(item_ids) > 1:
        cuts = np.flatnonzero(item_ids[1:] != item_ids[:-1]) + 1
    else:
        cuts = np.array([], dtype=np.int64)
    starts = np.concatenate((np.array([0], dtype=np.int64), cuts))
    ends = np.concatenate((cuts, np.array([len(item_ids)], dtype=np.int64)))
    return {
        int(item_ids[s]): int(amounts[s:e].sum())
        for s, e in zip(starts.tolist(), ends.tolist())
    }


def turnover_volume(amounts: np.ndarray) -> int:
    return int(amounts.sum()) if len(amounts) else 0


def turnover_rate(trans_value: int, market_total: int) -> float | None:
    if market_total <= 0:
        return None
    return float(np.round(trans_value * 100.0 / market_total, 2))


def sold_unique_items(item_ids: np.ndarray) -> int:
    return int(np.unique(item_ids).size) if len(item_ids) else 0


def _cull_jump_outliers_with_weights(
    values: np.ndarray, weights: np.ndarray, jump: float
) -> tuple[np.ndarray, np.ndarray]:
    """Keep the densest cluster of sorted values (gap > jump splits clusters)."""
    if len(values) == 0:
        return values, weights
    if len(values) == 1:
        return values, weights
    order = np.argsort(values)
    sorted_v = values[order]
    sorted_w = weights[order]
    bounds = [0]
    for i in range(len(sorted_v) - 1):
        if sorted_v[i + 1] - sorted_v[i] > jump:
            bounds.append(i + 1)
    bounds.append(len(sorted_v))
    clusters = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    best = max(clusters, key=lambda se: se[1] - se[0])
    return sorted_v[best[0] : best[1]], sorted_w[best[0] : best[1]]


def inflation_rate(
    current_prices: dict[int, int],
    previous_prices: dict[int, int],
    current_volumes: dict[int, int],
    volume_min: int = INFLATION_VOLUME_MIN,
) -> float | None:
    """
    Volume-weighted mean % price change for items with volume >= volume_min.

    Median of per-item % changes is useless here: market_price is the cluster
    floor, so most liquid items sit at 0% hour-to-hour and the median sticks
    at 0. Weighting by volume after the jump cull tracks the basket instead.
    """
    changes: list[float] = []
    weights: list[float] = []
    for item_id, vol in current_volumes.items():
        if vol < volume_min:
            continue
        cur = current_prices.get(item_id)
        prev = previous_prices.get(item_id)
        if cur is None or prev is None or prev <= 0:
            continue
        changes.append((cur - prev) * 100.0 / prev)
        weights.append(float(vol))

    if not changes:
        return None

    arr = np.asarray(changes, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    culled, culled_w = _cull_jump_outliers_with_weights(
        arr, w, INFLATION_CHANGE_JUMP
    )
    if len(culled) == 0 or culled_w.sum() <= 0:
        return None
    return float(np.round(float((culled * culled_w).sum() / culled_w.sum()), 2))
