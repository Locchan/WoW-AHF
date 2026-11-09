from datetime import datetime, timezone

from pymysql.connections import Connection

from wowahf.blizzard.ah_fetcher import BlizzardApiClient
from wowahf.db.writes import list_items_pending_names, set_item_name

# Cap name lookups per parse so a cold catalog cannot stall the AH ingest.
NAME_FETCH_LIMIT = 300


def resolve_pending_item_names(
    conn: Connection,
    api: BlizzardApiClient,
    *,
    limit: int = NAME_FETCH_LIMIT,
) -> tuple[int, int]:
    """
    Resolve names via static-forever /data/wow/item/{id}.
    Returns (ok_count, missing_count). 404s are marked missing and not retried.
    """
    pending = list_items_pending_names(conn, limit=limit)
    if not pending:
        return 0, 0

    now = datetime.now(timezone.utc)
    ok = 0
    missing = 0
    for item_id in pending:
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
