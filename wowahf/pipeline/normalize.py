from typing import Any, NamedTuple

VALID_TIME_LEFT = frozenset({"SHORT", "MEDIUM", "LONG", "VERY_LONG"})


class ListingRow(NamedTuple):
    auc_id: int
    item_id: int
    price: int
    auc_amount: int
    auc_time_left: str
    is_commodity: bool


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
) -> list[ListingRow]:
    """
    Flatten auctions + commodities.
    price = buyout (auctions) or unit_price (commodities). Bid-only skipped.
    """
    rows: list[ListingRow] = []

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
        rows.append(
            ListingRow(int(auc_id), item_id, int(buyout), int(amount), tl, False)
        )

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
        rows.append(
            ListingRow(
                int(auc_id), item_id, int(unit_price), int(amount), tl, True
            )
        )

    # Blizzard occasionally repeats an auc_id in one dump; PK is (auc_id, parse_id).
    by_id: dict[int, ListingRow] = {}
    for row in rows:
        by_id[row.auc_id] = row
    return list(by_id.values())


def listing_value(price: int, amount: int, is_commodity: bool) -> int:
    """Copper to buy this entire listing."""
    return int(price) * int(amount) if is_commodity else int(price)


def unit_price(price: int, amount: int, is_commodity: bool) -> int:
    """Copper per unit for clustering / market_price."""
    if is_commodity:
        return int(price)
    qty = int(amount) if int(amount) > 0 else 1
    return int(price) // qty
