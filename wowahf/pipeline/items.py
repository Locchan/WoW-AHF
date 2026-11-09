from datetime import datetime, timezone

from pymysql.connections import Connection

from wowahf.blizzard.ah_fetcher import BlizzardApiClient
from wowahf.db.writes import (
    list_items_missing_names,
    list_items_pending_names,
    set_item_name,
)
from wowahf.utils.shutdown import stop_requested

NAME_FETCH_BATCH = 100


def _resolve_ids(
    conn: Connection,
    api: BlizzardApiClient,
    item_ids: list[int],
) -> tuple[int, int]:
    """Returns (ok_count, missing_count)."""
    if not item_ids:
        return 0, 0

    now = datetime.now(timezone.utc)
    ok = 0
    missing = 0
    for item_id in item_ids:
        if stop_requested():
            break
        doc = api.get_item(item_id)
        if doc is None:
            set_item_name(conn, item_id, None, missing=True, now=now)
            missing += 1
            continue
        name = doc.get("name")
        if not isinstance(name, str) or not name.strip():
            set_item_name(conn, item_id, None, missing=True, now=now)
            missing += 1
            continue
        set_item_name(conn, item_id, name.strip(), missing=False, now=now)
        ok += 1

    return ok, missing


def resolve_item_names(
    conn: Connection,
    api: BlizzardApiClient,
    *,
    pending_limit: int = NAME_FETCH_BATCH,
    missing_limit: int = NAME_FETCH_BATCH,
) -> tuple[int, int, int, int]:
    """
    Resolve up to pending_limit pending + missing_limit previously-missing ids.
    Returns (pending_ok, pending_missing, missing_ok, missing_still).
    """
    pending_ids = list_items_pending_names(conn, limit=pending_limit)
    pending_ok, pending_miss = _resolve_ids(conn, api, pending_ids)

    missing_ids = list_items_missing_names(conn, limit=missing_limit)
    missing_ok, missing_still = _resolve_ids(conn, api, missing_ids)

    return pending_ok, pending_miss, missing_ok, missing_still

