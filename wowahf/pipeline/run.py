from datetime import UTC, datetime

from wowahf.blizzard.ah_fetcher import iter_ah_houses, peek_ah_last_modified
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
    mark_parse_ok,
    previous_parse_lm,
    purge_parse_data,
    touch_parse,
    upsert_auction_house,
    upsert_items,
    upsert_watch,
)
from wowahf.pipeline.crunch import aggregate_by_item_price, compute_transactions
from wowahf.pipeline.normalize import normalize_listings
from wowahf.pipeline.schedule import PARSE_INTERVAL, lm_gap_ok, mark_parse_attempt
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def run_parse() -> int | bool:
    """
    Fetch AH data and persist. parses.parsed_at is Blizzard Last-Modified
    (newest across house responses), which the scheduler uses as LM+1h10m.

    Returns False (skip) when the snapshot LM has not advanced since the
    last successful parse. ok stays FALSE until the full run finishes.

    Watch + sale inference run only after every house has been fetched, so a
    mid-run failure cannot leave watch half-updated against a discarded parse.
    """
    log("parse: started")
    mark_parse_attempt()
    now = datetime.now(UTC)

    snapshot_at = peek_ah_last_modified()
    if snapshot_at is None:
        snapshot_at = naive_utc_second(now)
        log("parse: warning: no Last-Modified in responses; using wall clock")
    else:
        snapshot_at = naive_utc_second(snapshot_at)

    conn = connect()
    parse_id: int | None = None
    try:
        last_lm = last_parsed_at(conn)
        if last_lm is not None and snapshot_at <= naive_utc_second(last_lm):
            log(
                f"parse: snapshot unchanged "
                f"(LM={snapshot_at.isoformat()}), skip"
            )
            return False

        parse_id = create_parse(conn, snapshot_at)
        prev = previous_parse_lm(conn, parse_id)
        prev_id = prev[0] if prev else None
        infer_sales = prev is not None and lm_gap_ok(prev[1], snapshot_at)
        if prev is not None and not infer_sales:
            log(
                f"parse: LM gap too large for sale inference "
                f"(prev_id={prev_id} prev_lm={naive_utc_second(prev[1]).isoformat()} "
                f"lm={snapshot_at.isoformat()}); transactions skipped"
            )

        processed = 0
        newest = snapshot_at
        # auc_id -> time_left per house; kept until watch/trans after full fetch.
        house_lots: dict[int, dict[int, str]] = {}

        for house in iter_ah_houses():
            if stop_requested():
                log(
                    "parse: stop requested, leaving parse incomplete "
                    f"(parse_id={parse_id})"
                )
                return False

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

            house_lots[house_id] = {r.auc_id: r.auc_time_left for r in rows}

            house_lm = house.get("last_modified")
            if house_lm is not None:
                house_lm = naive_utc_second(house_lm)
                if house_lm > newest:
                    newest = house_lm
            processed += 1
            del rows

        # Fetch succeeded — now update watch and infer sales against a
        # complete current snapshot.
        for house_id, current_lots in house_lots.items():
            if stop_requested():
                log(
                    "parse: stop requested during watch/trans, leaving parse "
                    f"incomplete (parse_id={parse_id})"
                )
                return False

            upsert_watch(conn, house_id, current_lots, now)

            if prev_id is None or not infer_sales:
                continue

            prev_rows = load_raw_for_parse(conn, house_id, prev_id)
            if not prev_rows:
                log(
                    f"parse: warning: house={house_id} prev_parse={prev_id} "
                    "has no raw rows (purged?); transactions skipped"
                )
                continue
            watch = load_watch(conn, house_id)
            trans_rows = compute_transactions(
                prev_rows,
                set(current_lots),
                watch,
                parse_id,
                now,
            )
            insert_trans_rows(conn, house_id, trans_rows)

        touch_parse(conn, parse_id, newest)
        mark_parse_ok(conn, parse_id)
        next_due = newest + PARSE_INTERVAL
        log(
            f"parse: finished parse_id={parse_id} "
            f"houses={processed} "
            f"last_modified={newest.isoformat()} "
            f"next_due={next_due.isoformat()}"
        )
        return parse_id
    except Exception:
        if parse_id is not None:
            try:
                log(f"parse: failed; purging incomplete parse_id={parse_id}")
                purge_parse_data(conn, parse_id)
            except Exception as purge_exc:
                log(f"parse: purge after failure failed: {purge_exc}")
        raise
    finally:
        conn.close()
