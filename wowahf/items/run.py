from wowahf.blizzard.ah_fetcher import get_blizzard_api_obj
from wowahf.db.connection import connect
from wowahf.pipeline.items import NAME_FETCH_BATCH, resolve_item_names
from wowahf.utils.log import log


def run_item_names(
    *,
    pending_limit: int = NAME_FETCH_BATCH,
    missing_limit: int = NAME_FETCH_BATCH,
) -> None:
    """Fetch a batch of pending and previously-missing item names from the API."""
    log("items: started")
    conn = connect()
    try:
        api = get_blizzard_api_obj()
        pending_ok, pending_miss, missing_ok, missing_still = resolve_item_names(
            conn,
            api,
            pending_limit=pending_limit,
            missing_limit=missing_limit,
        )
        log(
            f"items: finished "
            f"pending_ok={pending_ok} pending_missing={pending_miss} "
            f"missing_recovered={missing_ok} missing_still={missing_still}"
        )
    finally:
        conn.close()
