from datetime import datetime, timezone

from wowahf.blizzard.ah_fetcher import (
    get_ah_data,
    get_blizzard_api_obj,
)
from wowahf.db.connection import connect
from wowahf.db.migrate import migrate
from wowahf.db.writes import (
    create_parse,
    insert_aggr_rows,
    insert_raw_rows,
    insert_trans_rows,
    load_raw_for_parse,
    load_watch,
    previous_parse_id,
    touch_parse,
    upsert_auction_house,
    upsert_watch,
)
from wowahf.pipeline.crunch import aggregate_by_item_price, compute_transactions
from wowahf.pipeline.normalize import normalize_listings


def run_parse() -> int:
    """Fetch AH data, migrate schema, crunch, and persist. Returns parse_id."""
    conn = connect()
    try:
        migrate(conn)

        now = datetime.now(timezone.utc)
        parse_id = create_parse(conn, now)
        print(f"pipeline: parse_id={parse_id}")

        # Warm OAuth before heavy fetch
        get_blizzard_api_obj()
        data = get_ah_data(include_listings=True)
        prev_id = previous_parse_id(conn, parse_id)

        for house in data["houses"]:
            house_id = int(house["id"])
            name = str(house.get("name") or house_id)
            upsert_auction_house(conn, house_id, name)

            rows = normalize_listings(house.get("auctions"), house.get("commodities"))
            print(
                f"pipeline: house {house_id} ({name}): "
                f"{len(rows)} normalized listings"
            )

            raw_db_rows = [
                (auc_id, parse_id, item_id, price, amount, tl)
                for auc_id, item_id, price, amount, tl in rows
            ]
            insert_raw_rows(conn, house_id, raw_db_rows)

            aggr_rows = aggregate_by_item_price(rows, parse_id)
            insert_aggr_rows(conn, house_id, aggr_rows)
            print(f"pipeline: house {house_id}: {len(aggr_rows)} aggr rows")

            current_lots = {auc_id: tl for auc_id, _i, _p, _a, tl in rows}
            upsert_watch(conn, house_id, current_lots, now)

            if prev_id is None:
                print(f"pipeline: house {house_id}: no previous parse, skip trans")
                continue

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
            print(f"pipeline: house {house_id}: {len(trans_rows)} trans rows")

        touch_parse(conn, parse_id, datetime.now(timezone.utc))
        print(f"pipeline: done parse_id={parse_id}")
        return parse_id
    finally:
        conn.close()
