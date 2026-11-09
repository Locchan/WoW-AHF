from datetime import datetime, timezone

from wowahf.blizzard.ah_fetcher import get_ah_data, peek_ah_snapshot_time
from wowahf.db.connection import connect
from wowahf.db.writes import (
    backfill_raw_is_commodity_from_items,
    create_parse,
    insert_aggr_rows,
    insert_raw_rows,
    insert_trans_rows,
    last_parsed_at,
    list_house_ids,
    load_raw_for_parse,
    load_watch,
    previous_parse_id,
    touch_parse,
    upsert_auction_house,
    upsert_items,
    upsert_watch,
)
from wowahf.pipeline.crunch import aggregate_by_item_price, compute_transactions
from wowahf.pipeline.normalize import normalize_listings
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def run_parse() -> int | bool:
    """
    Fetch AH data when Blizzard Last-Modified advanced.

    Returns parse_id on a full run, or False if the snapshot is unchanged
    (so the job manager skips chaining stats).
    """
    log("parse: started")
    conn = connect()
    try:
        known_houses = list_house_ids(conn)
        snapshot_at = peek_ah_snapshot_time(known_houses or None)
        if snapshot_at is None:
            log("parse: no Last-Modified from HEAD; aborting")
            return False

        last = last_parsed_at(conn)
        last_sec = naive_utc_second(last) if last is not None else None
        if last_sec is not None and snapshot_at == last_sec:
            log(
                f"parse: skip unchanged snapshot last_modified={snapshot_at.isoformat()}"
            )
            return False

        log(
            f"parse: new snapshot last_modified={snapshot_at.isoformat()} "
            f"prev={last_sec.isoformat() if last_sec else None}"
        )

        # Wall clock only for watch/trans bookkeeping; parses.parsed_at is LM.
        now = datetime.now(timezone.utc)
        parse_id = create_parse(conn, snapshot_at)

        data = get_ah_data(include_listings=True)
        prev_id = previous_parse_id(conn, parse_id)
        houses = data["houses"]
        processed = 0

        for house in houses:
            if stop_requested():
                log("parse: stop requested, leaving remaining houses for next run")
                break

            house_id = int(house["id"])
            name = str(house.get("name") or house_id)
            upsert_auction_house(conn, house_id, name)

            rows = normalize_listings(house.get("auctions"), house.get("commodities"))
            insert_raw_rows(conn, house_id, parse_id, rows)

            commodity_ids = [r.item_id for r in rows if r.is_commodity]
            auction_ids = [r.item_id for r in rows if not r.is_commodity]
            upsert_items(conn, commodity_ids, auction_ids, now)
            backfill_raw_is_commodity_from_items(
                conn, house_id, commodity_ids + auction_ids
            )

            aggr_rows = aggregate_by_item_price(rows, parse_id)
            insert_aggr_rows(conn, house_id, aggr_rows)

            current_lots = {r.auc_id: r.auc_time_left for r in rows}
            upsert_watch(conn, house_id, current_lots, now)

            if prev_id is not None:
                prev_rows = load_raw_for_parse(conn, house_id, prev_id)
                watch = load_watch(conn, house_id)
                trans_rows = compute_transactions(
                    prev_rows,
                    set(current_lots),
                    watch,
                    parse_id,
                    now,
                )
                insert_trans_rows(conn, house_id, trans_rows)

            processed += 1

        # Keep parsed_at as the Blizzard snapshot time (not wall clock).
        touch_parse(conn, parse_id, snapshot_at)
        log(
            f"parse: finished parse_id={parse_id} "
            f"houses={processed}/{len(houses)} "
            f"last_modified={snapshot_at.isoformat()}"
        )
        return parse_id
    finally:
        conn.close()
