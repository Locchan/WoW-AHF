from datetime import datetime, timedelta

import numpy as np

TRACKABLE_FIRST = frozenset({"LONG", "VERY_LONG"})
SHORT_MAX = timedelta(minutes=30)
EXCLUDE_BEFORE_EXPIRY = timedelta(hours=1)


def aggregate_by_item_price(
    rows: list[tuple[int, int, int, int, str]],
    parse_id: int,
) -> list[tuple[int, int, int, int]]:
    """
    rows: (auc_id, item_id, price, auc_amount, time_left)
    returns: (item_id, parse_id, price, amount)
    """
    if not rows:
        return []

    item_ids = np.fromiter((r[1] for r in rows), dtype=np.int64, count=len(rows))
    prices = np.fromiter((r[2] for r in rows), dtype=np.int64, count=len(rows))
    amounts = np.fromiter((r[3] for r in rows), dtype=np.int64, count=len(rows))

    # Pack item_id|price into a structured view for unique grouping.
    dtype = np.dtype([("item_id", np.int64), ("price", np.int64)])
    keys = np.empty(len(rows), dtype=dtype)
    keys["item_id"] = item_ids
    keys["price"] = prices

    order = np.argsort(keys, order=("item_id", "price"))
    keys_sorted = keys[order]
    amounts_sorted = amounts[order]

    # Boundaries where (item_id, price) changes
    if len(keys_sorted) == 1:
        changes = np.array([0])
    else:
        changed = (keys_sorted["item_id"][1:] != keys_sorted["item_id"][:-1]) | (
            keys_sorted["price"][1:] != keys_sorted["price"][:-1]
        )
        changes = np.flatnonzero(changed) + 1
        changes = np.concatenate(([0], changes))

    ends = np.concatenate((changes[1:], [len(keys_sorted)]))
    out: list[tuple[int, int, int, int]] = []
    for start, end in zip(changes, ends):
        item_id = int(keys_sorted["item_id"][start])
        price = int(keys_sorted["price"][start])
        amount = int(amounts_sorted[start:end].sum())
        out.append((item_id, parse_id, price, amount))
    return out


def is_sale_eligible(
    first_time_left: str,
    short_seen_at: datetime | None,
    disappeared_at: datetime,
) -> bool:
    """Tracked only if first seen LONG/VERY_LONG and not in pre-expiry window."""
    if first_time_left not in TRACKABLE_FIRST:
        return False
    if short_seen_at is None:
        return True
    short_at = short_seen_at.replace(tzinfo=None) if short_seen_at.tzinfo else short_seen_at
    gone_at = disappeared_at.replace(tzinfo=None) if disappeared_at.tzinfo else disappeared_at
    expiry_at = short_at + SHORT_MAX
    cutoff = expiry_at - EXCLUDE_BEFORE_EXPIRY
    # Disappeared at/after cutoff => treat as expired, not sold.
    return gone_at < cutoff


def compute_transactions(
    prev_rows: list[tuple[int, int, int, int, str]],
    current_auc_ids: set[int],
    watch: dict[int, tuple[str, datetime | None, bool]],
    parse_id: int,
    disappeared_at: datetime,
) -> list[tuple[int, int, int, int]]:
    """
    prev_rows: (auc_id, item_id, price, auc_amount, time_left)
    returns aggregated sold rows: (item_id, parse_id, price, amount)
    """
    if not prev_rows:
        return []

    sold: list[tuple[int, int, int]] = []  # item_id, price, amount
    for auc_id, item_id, price, amount, _tl in prev_rows:
        if auc_id in current_auc_ids:
            continue
        meta = watch.get(auc_id)
        if meta is None:
            continue
        first_tl, short_at, _active = meta
        if not is_sale_eligible(first_tl, short_at, disappeared_at):
            continue
        sold.append((item_id, price, amount))

    if not sold:
        return []

    item_ids = np.fromiter((r[0] for r in sold), dtype=np.int64, count=len(sold))
    prices = np.fromiter((r[1] for r in sold), dtype=np.int64, count=len(sold))
    amounts = np.fromiter((r[2] for r in sold), dtype=np.int64, count=len(sold))

    dtype = np.dtype([("item_id", np.int64), ("price", np.int64)])
    keys = np.empty(len(sold), dtype=dtype)
    keys["item_id"] = item_ids
    keys["price"] = prices
    order = np.argsort(keys, order=("item_id", "price"))
    keys_sorted = keys[order]
    amounts_sorted = amounts[order]

    if len(keys_sorted) == 1:
        changes = np.array([0])
    else:
        changed = (keys_sorted["item_id"][1:] != keys_sorted["item_id"][:-1]) | (
            keys_sorted["price"][1:] != keys_sorted["price"][:-1]
        )
        changes = np.concatenate(([0], np.flatnonzero(changed) + 1))

    ends = np.concatenate((changes[1:], [len(keys_sorted)]))
    out: list[tuple[int, int, int, int]] = []
    for start, end in zip(changes, ends):
        out.append(
            (
                int(keys_sorted["item_id"][start]),
                parse_id,
                int(keys_sorted["price"][start]),
                int(amounts_sorted[start:end].sum()),
            )
        )
    return out
