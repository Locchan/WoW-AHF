from datetime import UTC, datetime, timedelta

from wowahf.blizzard.ah_fetcher import get_ah_data
from wowahf.db.connection import connect
from wowahf.db.writes import (
    backfill_raw_is_commodity_from_items,
    create_parse,
    insert_aggr_rows,
    insert_raw_rows,
    insert_trans_rows,
    last_parsed_at,
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
from wowahf.pipeline.schedule import PARSE_INTERVAL, mark_parse_attempt
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def run_parse() -> int | bool:
    """
    Fetch AH data and persist. parses.parsed_at is Blizzard Last-Modified
    (newest across house responses), which the scheduler uses as LM+1h10m.

    Returns False (skip) when the snapshot LM has not advanced since the
    last stored parse, so we do not invent duplicate points.
    """
    log("parse: started")
    mark_parse_attempt()
    now = datetime.now(UTC)

    data = get_ah_data(include_listings=True)
    snapshot_at = data.get("last_modified")
    if snapshot_at is not None:
        snapshot_at = naive_utc_second(snapshot_at)
    else:
        snapshot_at = naive_utc_second(now)
        log("parse: warning: no Last-Modified in responses; using wall clock")

    conn = connect()
    try:
        last_lm = last_parsed_at(conn)
        if last_lm is not None and snapshot_at <= naive_utc_second(last_lm):
            log(
                f"parse: snapshot unchanged "
                f"(LM={snapshot_at.isoformat()}), skip"
            )
            return False

        parse_id = create_parse(conn, snapshot_at)
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
                if not prev_rows:
                    log(
                        f"parse: warning: house={house_id} prev_parse={prev_id} "
                        "has no raw rows (purged?); transactions skipped"
                    )
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

        touch_parse(conn, parse_id, snapshot_at)
        next_due = snapshot_at + PARSE_INTERVAL
        log(
            f"parse: finished parse_id={parse_id} "
            f"houses={processed}/{len(houses)} "
            f"last_modified={snapshot_at.isoformat()} "
            f"next_due={next_due.isoformat()}"
        )
        return parse_id
    finally:
        conn.close()
