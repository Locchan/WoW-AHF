"""Ingest a durable gzip snapshot into house tables + watch/trans."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

from psycopg import Connection

from wowahf.db.writes import (
    backfill_raw_is_commodity_from_items,
    delete_delta_metrics,
    has_unrecoverable_parse_gap,
    insert_aggr_rows,
    insert_raw_rows,
    insert_trans_rows,
    list_house_ids,
    list_ok_parse_ids_from,
    load_raw_for_parse,
    load_watch,
    mark_parse_ok,
    previous_parse_lm,
    purge_parse_rows,
    touch_parse,
    upsert_auction_house,
    upsert_items,
    upsert_watch,
)
from wowahf.pipeline.crunch import aggregate_by_item_price, compute_transactions
from wowahf.pipeline.normalize import normalize_listings
from wowahf.pipeline.schedule import delta_metrics_ok
from wowahf.pipeline.snapshot import cleanup_old_snapshots, iter_snapshot_houses
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def purge_parse_rows_trans_only(conn: Connection, parse_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'data_transactions'
              AND tablename ~ '^ah_trans_[0-9]+$'
            """
        )
        for (table,) in cur.fetchall():
            cur.execute(
                f"DELETE FROM data_transactions.{table} WHERE parse_id = %s",
                (parse_id,),
            )
    conn.commit()


def invalidate_metrics_from(conn: Connection, parse_id: int) -> None:
    """
    Drop delta metrics / ah_trans for this parse and every later ok parse so
    stats can recompute after a failed parse is recovered into the series.
    """
    from wowahf.pipeline import backfill_trans

    ids = list_ok_parse_ids_from(conn, parse_id)
    house_ids = list_house_ids(conn)
    for pid in ids:
        purge_parse_rows_trans_only(conn, pid)
        for house_id in house_ids:
            delete_delta_metrics(conn, pid, house_id)
        backfill_trans.clear_attempts_from(pid)
    log(f"ingest: invalidated deltas from parse_id>={parse_id} n={len(ids)}")


def ingest_snapshot(
    conn: Connection,
    parse_id: int,
    *,
    now: datetime,
    houses: Iterator[dict] | None = None,
) -> int:
    """
    Load gzip snapshot into DB and mark parse ok.

    Clears any partial rows for this parse_id first. Returns houses processed.
    """
    purge_parse_rows(conn, parse_id)

    snapshot_at = None
    with conn.cursor() as cur:
        cur.execute("SELECT parsed_at FROM parses WHERE id = %s", (parse_id,))
        row = cur.fetchone()
        if row:
            snapshot_at = naive_utc_second(row[0])

    prev = previous_parse_lm(conn, parse_id)
    prev_id = prev[0] if prev else None
    failed_in_gap = False
    if prev_id is not None:
        failed_in_gap = has_unrecoverable_parse_gap(conn, prev_id, parse_id)
    infer_sales = delta_metrics_ok(
        prev[1] if prev else None,
        snapshot_at or now,
        parse_failed_in_gap=failed_in_gap,
    )
    if prev is not None and not infer_sales:
        log(
            f"ingest: skipping sale inference after unrecoverable gap "
            f"(parse_id={parse_id} prev_id={prev_id})"
        )

    house_iter = houses if houses is not None else iter_snapshot_houses(parse_id)
    processed = 0
    newest = snapshot_at
    house_lots: dict[int, dict[int, str]] = {}

    for house in house_iter:
        if stop_requested():
            raise RuntimeError("stop requested during ingest")

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
            if newest is None or house_lm > newest:
                newest = house_lm
        processed += 1
        del rows

    for house_id, current_lots in house_lots.items():
        if stop_requested():
            raise RuntimeError("stop requested during watch/trans")
        upsert_watch(conn, house_id, current_lots, now)
        if prev_id is None or not infer_sales:
            continue
        prev_rows = load_raw_for_parse(conn, house_id, prev_id)
        if not prev_rows:
            log(
                f"ingest: warning: house={house_id} prev_parse={prev_id} "
                "has no raw rows; transactions skipped"
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

    if newest is not None:
        touch_parse(conn, parse_id, newest)
    mark_parse_ok(conn, parse_id)
    cleanup_old_snapshots()

    log(f"ingest: finished parse_id={parse_id} houses={processed}")
    return processed
