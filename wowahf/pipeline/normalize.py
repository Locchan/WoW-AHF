from typing import Any

VALID_TIME_LEFT = frozenset({"SHORT", "MEDIUM", "LONG", "VERY_LONG"})


def _time_left(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("type") or value.get("name")
    if not isinstance(value, str):
        return None
    key = value.strip().upper().replace(" ", "_")
    return key if key in VALID_TIME_LEFT else None


def _item_id(item: Any) -> int | None:
    if isinstance(item, dict):
        raw = item.get("id")
        return int(raw) if raw is not None else None
    return None


def normalize_listings(
    auctions: list[dict[str, Any]] | None,
    commodities: list[dict[str, Any]] | None,
) -> list[tuple[int, int, int, int, str]]:
    """
    Flatten auctions + commodities into rows:
    (auc_id, item_id, price, auc_amount, auc_time_left)
    price = buyout (auctions) or unit_price (commodities). Bid-only skipped.
    """
    rows: list[tuple[int, int, int, int, str]] = []

    for entry in auctions or []:
        buyout = entry.get("buyout")
        if buyout is None:
            continue
        auc_id = entry.get("id")
        item_id = _item_id(entry.get("item"))
        amount = entry.get("quantity", 1)
        tl = _time_left(entry.get("time_left"))
        if auc_id is None or item_id is None or tl is None:
            continue
        rows.append((int(auc_id), item_id, int(buyout), int(amount), tl))

    for entry in commodities or []:
        unit_price = entry.get("unit_price")
        if unit_price is None:
            continue
        auc_id = entry.get("id")
        item_id = _item_id(entry.get("item"))
        amount = entry.get("quantity", 1)
        tl = _time_left(entry.get("time_left"))
        if auc_id is None or item_id is None or tl is None:
            continue
        rows.append((int(auc_id), item_id, int(unit_price), int(amount), tl))

    return rows
