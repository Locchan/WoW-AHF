from datetime import datetime
from typing import Sequence

from psycopg import Connection

from wowahf.db.house_tables import (
    aggr_table,
    ensure_house_tables,
    raw_table,
    trans_table,
    watch_table,
)
from wowahf.pipeline.normalize import ListingRow

BATCH_SIZE = 2000


def _batched(rows: Sequence, size: int = BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def upsert_auction_house(conn: Connection, house_id: int, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO auction_houses (id, name) VALUES (%s, %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """,
            (house_id, name),
        )
    conn.commit()


def _naive_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def create_parse(conn: Connection, parsed_at: datetime) -> int:
    """Insert an in-progress parse (ok=FALSE until mark_parse_ok)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO parses (parsed_at, ok) VALUES (%s, FALSE) RETURNING id",
            (_naive_utc(parsed_at),),
        )
        parse_id = cur.fetchone()[0]
    conn.commit()
    return int(parse_id)


def touch_parse(conn: Connection, parse_id: int, parsed_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE parses SET parsed_at = %s WHERE id = %s",
            (_naive_utc(parsed_at), parse_id),
        )
    conn.commit()


def mark_parse_ok(conn: Connection, parse_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE parses SET ok = TRUE WHERE id = %s",
            (parse_id,),
        )
    conn.commit()


def previous_parse_id(conn: Connection, current_parse_id: int) -> int | None:
    """Previous successful parse (incomplete rows are ignored)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM parses
            WHERE id < %s AND ok
            ORDER BY id DESC LIMIT 1
            """,
            (current_parse_id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def last_parsed_at(conn: Connection) -> datetime | None:
    """Last-Modified of the newest successful parse (drives schedule)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT parsed_at FROM parses WHERE ok ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row[0] if row else None


def list_incomplete_parse_ids(conn: Connection) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM parses WHERE NOT ok ORDER BY id ASC")
        return [int(r[0]) for r in cur.fetchall()]


def purge_parse_data(conn: Connection, parse_id: int) -> None:
    """Delete a parse and every row keyed by that parse_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE (schemaname = 'data_raw' AND tablename ~ '^ah_raw_[0-9]+$')
               OR (schemaname = 'data_aggregated'
                   AND tablename ~ '^ah_aggr_[0-9]+$')
               OR (schemaname = 'data_transactions'
                   AND tablename ~ '^ah_trans_[0-9]+$')
            ORDER BY 1, 2
            """
        )
        house_tables = [(s, t) for s, t in cur.fetchall()]
        for schema, table in house_tables:
            cur.execute(
                f"DELETE FROM {schema}.{table} WHERE parse_id = %s",
                (parse_id,),
            )
        for table in (
            "market_stats",
            "market_price",
            "market_depth",
            "market_spread",
            "item_transaction_stats",
        ):
            cur.execute(
                f"DELETE FROM public.{table} WHERE parse_id = %s",
                (parse_id,),
            )
        cur.execute("DELETE FROM parses WHERE id = %s", (parse_id,))
    conn.commit()


def begin_job_run(conn: Connection, job_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO job_runs (job_name, started_at, ok)
            VALUES (%s, clock_timestamp(), FALSE)
            RETURNING id
            """,
            (job_name,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return int(run_id)


def finish_job_run(conn: Connection, run_id: int, *, ok: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE job_runs
            SET finished_at = clock_timestamp(), ok = %s
            WHERE id = %s
            """,
            (ok, run_id),
        )
    conn.commit()


def list_incomplete_job_run_ids(conn: Connection) -> list[tuple[int, str]]:
    """Latest run per job_name when that run did not finish ok."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (job_name) id, job_name
            FROM job_runs
            ORDER BY job_name, id DESC
            """
        )
        latest = [(int(i), str(n)) for i, n in cur.fetchall()]
        incomplete: list[tuple[int, str]] = []
        for run_id, name in latest:
            cur.execute("SELECT ok FROM job_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            if row is not None and not bool(row[0]):
                incomplete.append((run_id, name))
        return incomplete


def delete_job_run(conn: Connection, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM job_runs WHERE id = %s", (run_id,))
    conn.commit()


def list_parse_ids(conn: Connection) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM parses WHERE ok ORDER BY id ASC")
        return [int(r[0]) for r in cur.fetchall()]


def list_house_ids(conn: Connection) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM auction_houses ORDER BY id ASC")
        return [int(r[0]) for r in cur.fetchall()]


def insert_raw_rows(
    conn: Connection,
    house_id: int,
    parse_id: int,
    rows: Sequence[ListingRow],
) -> None:
    ensure_house_tables(conn, house_id)
    sql = (
        f"INSERT INTO {raw_table(house_id)} "
        "(auc_id, parse_id, item_id, price, auc_amount, auc_time_left, is_commodity) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    payload = [
        (
            r.auc_id,
            parse_id,
            r.item_id,
            r.price,
            r.auc_amount,
            r.auc_time_left,
            bool(r.is_commodity),
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        for batch in _batched(payload):
            cur.executemany(sql, batch)
    conn.commit()


def upsert_items(
    conn: Connection,
    commodity_item_ids: Sequence[int],
    auction_item_ids: Sequence[int],
    now: datetime,
) -> None:
    """
    Permanent item catalog. is_commodity is keyed by item_id.
    Commodity wins if an id appears in both feeds.
    """
    now_naive = _naive_utc(now)
    commodity_ids = sorted({int(i) for i in commodity_item_ids})
    auction_ids = sorted(
        {int(i) for i in auction_item_ids if int(i) not in set(commodity_ids)}
    )
    rows = [(i, True, now_naive) for i in commodity_ids] + [
        (i, False, now_naive) for i in auction_ids
    ]
    if not rows:
        return
    sql = """
        INSERT INTO items (id, is_commodity, name_status, updated_at)
        VALUES (%s, %s, 'pending', %s)
        ON CONFLICT (id) DO UPDATE SET
            is_commodity = EXCLUDED.is_commodity,
            updated_at = EXCLUDED.updated_at
    """
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()


def backfill_raw_is_commodity_from_items(
    conn: Connection,
    house_id: int,
    item_ids: Sequence[int],
) -> int:
    """
    Copy items.is_commodity onto ah_raw_* for the given item_ids only
    (index-friendly; does not scan the whole raw table).
    """
    ids = sorted({int(i) for i in item_ids})
    if not ids:
        return 0
    ensure_house_tables(conn, house_id)
    table = raw_table(house_id)
    updated = 0
    with conn.cursor() as cur:
        for batch in _batched(ids):
            cur.execute(
                f"""
                UPDATE {table} AS r
                SET is_commodity = i.is_commodity
                FROM items AS i
                WHERE i.id = r.item_id
                  AND r.item_id = ANY(%s)
                  AND r.is_commodity IS DISTINCT FROM i.is_commodity
                """,
                (list(batch),),
            )
            updated += cur.rowcount
    conn.commit()
    return int(updated)


def list_items_by_name_status(
    conn: Connection, status: str, limit: int = 200
) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM items
            WHERE name_status = %s
            ORDER BY updated_at ASC, id ASC
            LIMIT %s
            """,
            (status, limit),
        )
        return [int(r[0]) for r in cur.fetchall()]


def list_items_pending_names(conn: Connection, limit: int = 200) -> list[int]:
    return list_items_by_name_status(conn, "pending", limit=limit)


def list_items_missing_names(conn: Connection, limit: int = 200) -> list[int]:
    return list_items_by_name_status(conn, "missing", limit=limit)


def set_item_name(
    conn: Connection,
    item_id: int,
    name: str | None,
    *,
    missing: bool,
    now: datetime,
) -> None:
    status = "missing" if missing else "ok"
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET name = %s, name_status = %s, updated_at = %s
            WHERE id = %s
            """,
            (name, status, _naive_utc(now), item_id),
        )
    conn.commit()


def _parsed_at_for(conn: Connection, parse_id: int) -> datetime:
    with conn.cursor() as cur:
        cur.execute("SELECT parsed_at FROM parses WHERE id = %s", (parse_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"parse_id {parse_id} not found")
    return row[0]


def insert_aggr_rows(
    conn: Connection,
    house_id: int,
    rows: Sequence[tuple[int, int, int, int]],
) -> None:
    """rows: (item_id, parse_id, price, amount)"""
    if not rows:
        return
    ensure_house_tables(conn, house_id)
    # All rows in one call share a parse_id today; still group safely.
    by_parse: dict[int, list[tuple[int, int, int, int]]] = {}
    for row in rows:
        by_parse.setdefault(row[1], []).append(row)
    sql = (
        f"INSERT INTO {aggr_table(house_id)} "
        "(item_id, parse_id, parsed_at, price, amount) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for parse_id, group in by_parse.items():
            parsed_at = _parsed_at_for(conn, parse_id)
            payload = [
                (item_id, parse_id, parsed_at, price, amount)
                for item_id, _pid, price, amount in group
            ]
            for batch in _batched(payload):
                cur.executemany(sql, batch)
    conn.commit()


def insert_trans_rows(
    conn: Connection,
    house_id: int,
    rows: Sequence[tuple[int, int, int, int, int, int]],
) -> None:
    """rows: (item_id, parse_id, price, amount, is_commodity, lots)"""
    if not rows:
        return
    ensure_house_tables(conn, house_id)
    by_parse: dict[int, list[tuple[int, int, int, int, int, int]]] = {}
    for row in rows:
        by_parse.setdefault(row[1], []).append(row)
    sql = (
        f"INSERT INTO {trans_table(house_id)} "
        "(item_id, parse_id, parsed_at, price, amount, is_commodity, lots) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for parse_id, group in by_parse.items():
            parsed_at = _parsed_at_for(conn, parse_id)
            payload = [
                (
                    item_id,
                    parse_id,
                    parsed_at,
                    price,
                    amount,
                    bool(is_commodity),
                    int(lots),
                )
                for item_id, _pid, price, amount, is_commodity, lots in group
            ]
            for batch in _batched(payload):
                cur.executemany(sql, batch)
    conn.commit()


def load_raw_for_parse(
    conn: Connection, house_id: int, parse_id: int
) -> list[tuple[int, int, int, int, str, int]]:
    """(auc_id, item_id, price, auc_amount, auc_time_left, is_commodity)."""
    ensure_house_tables(conn, house_id)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT auc_id, item_id, price, auc_amount, auc_time_left, is_commodity "
            f"FROM {raw_table(house_id)} WHERE parse_id = %s",
            (parse_id,),
        )
        out: list[tuple[int, int, int, int, str, int]] = []
        for auc_id, item_id, price, amount, tl, is_commodity in cur.fetchall():
            out.append(
                (
                    int(auc_id),
                    int(item_id),
                    int(price),
                    int(amount),
                    str(tl),
                    1 if is_commodity else 0,
                )
            )
        return out


def load_trans_for_parse(
    conn: Connection, house_id: int, parse_id: int
) -> list[tuple[int, int, int, int, int]]:
    """(item_id, price, amount, is_commodity, lots)."""
    ensure_house_tables(conn, house_id)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT item_id, price, amount, is_commodity, lots "
            f"FROM {trans_table(house_id)} WHERE parse_id = %s",
            (parse_id,),
        )
        return [
            (
                int(item_id),
                int(price),
                int(amount),
                1 if is_commodity else 0,
                int(lots),
            )
            for item_id, price, amount, is_commodity, lots in cur.fetchall()
        ]


def load_watch(
    conn: Connection, house_id: int
) -> dict[int, tuple[str, datetime | None, bool]]:
    ensure_house_tables(conn, house_id)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT auc_id, first_time_left, short_seen_at, active "
            f"FROM {watch_table(house_id)}"
        )
        return {
            int(auc_id): (str(first_tl), short_at, bool(active))
            for auc_id, first_tl, short_at, active in cur.fetchall()
        }


def upsert_watch(
    conn: Connection,
    house_id: int,
    current_lots: dict[int, str],
    now: datetime,
) -> None:
    ensure_house_tables(conn, house_id)
    existing = load_watch(conn, house_id)
    now_naive = now.replace(tzinfo=None) if now.tzinfo else now
    table = watch_table(house_id)

    inserts: list[tuple] = []
    short_updates: list[tuple] = []
    reactivate: list[int] = []

    for auc_id, time_left in current_lots.items():
        if auc_id not in existing:
            short_at = now_naive if time_left == "SHORT" else None
            inserts.append((auc_id, time_left, short_at, True))
            continue
        _first_tl, short_at, active = existing[auc_id]
        if short_at is None and time_left == "SHORT":
            short_updates.append((now_naive, auc_id))
        if not active:
            reactivate.append(auc_id)

    missing = [
        auc_id
        for auc_id in existing
        if auc_id not in current_lots and existing[auc_id][2]
    ]

    with conn.cursor() as cur:
        if inserts:
            cur.executemany(
                f"INSERT INTO {table} (auc_id, first_time_left, short_seen_at, active) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (auc_id) DO NOTHING",
                inserts,
            )
        if short_updates:
            cur.executemany(
                f"UPDATE {table} SET short_seen_at = %s "
                "WHERE auc_id = %s AND short_seen_at IS NULL",
                short_updates,
            )
        if reactivate:
            cur.execute(
                f"UPDATE {table} SET active = TRUE WHERE auc_id = ANY(%s)",
                (reactivate,),
            )
        if missing:
            cur.execute(
                f"UPDATE {table} SET active = FALSE WHERE auc_id = ANY(%s)",
                (missing,),
            )
    conn.commit()


def metric_exists(
    conn: Connection, parse_id: int, ah_id: int, metric: str
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM market_stats
            WHERE parse_id = %s AND ah_id = %s AND metric = %s
            LIMIT 1
            """,
            (parse_id, ah_id, metric),
        )
        return cur.fetchone() is not None


def market_price_exists(conn: Connection, parse_id: int, ah_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM market_price
            WHERE parse_id = %s AND ah_id = %s
            LIMIT 1
            """,
            (parse_id, ah_id),
        )
        return cur.fetchone() is not None


def _insert_market_with_parsed_at(
    conn: Connection,
    sql: str,
    rows: Sequence[tuple],
    parse_id_index: int = 0,
) -> None:
    by_parse: dict[int, list[tuple]] = {}
    for row in rows:
        by_parse.setdefault(int(row[parse_id_index]), []).append(row)
    with conn.cursor() as cur:
        for parse_id, group in by_parse.items():
            parsed_at = _parsed_at_for(conn, parse_id)
            payload = [(*row, parsed_at) for row in group]
            for batch in _batched(payload):
                cur.executemany(sql, batch)
    conn.commit()


def insert_market_stats(
    conn: Connection,
    rows: Sequence[tuple[int, int, str, float | int]],
) -> None:
    """rows: (parse_id, ah_id, metric, value) — house-level metrics only."""
    if not rows:
        return
    _insert_market_with_parsed_at(
        conn,
        "INSERT INTO market_stats (parse_id, ah_id, metric, value, parsed_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        rows,
    )


def insert_market_prices(
    conn: Connection,
    rows: Sequence[tuple[int, int, int, int]],
) -> None:
    """rows: (parse_id, ah_id, item_id, price) — price in copper."""
    if not rows:
        return
    _insert_market_with_parsed_at(
        conn,
        "INSERT INTO market_price (parse_id, ah_id, item_id, price, parsed_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        rows,
    )


def market_depth_exists(conn: Connection, parse_id: int, ah_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM market_depth
            WHERE parse_id = %s AND ah_id = %s
            LIMIT 1
            """,
            (parse_id, ah_id),
        )
        return cur.fetchone() is not None


def insert_market_depths(
    conn: Connection,
    rows: Sequence[tuple[int, int, int, int]],
) -> None:
    """rows: (parse_id, ah_id, item_id, depth)"""
    if not rows:
        return
    _insert_market_with_parsed_at(
        conn,
        "INSERT INTO market_depth (parse_id, ah_id, item_id, depth, parsed_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        rows,
    )


def market_spread_exists(conn: Connection, parse_id: int, ah_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM market_spread
            WHERE parse_id = %s AND ah_id = %s
            LIMIT 1
            """,
            (parse_id, ah_id),
        )
        return cur.fetchone() is not None


def insert_market_spreads(
    conn: Connection,
    rows: Sequence[tuple[int, int, int, float]],
) -> None:
    """rows: (parse_id, ah_id, item_id, spread_pct)"""
    if not rows:
        return
    _insert_market_with_parsed_at(
        conn,
        "INSERT INTO market_spread (parse_id, ah_id, item_id, spread, parsed_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        rows,
    )


def item_transaction_stats_exists(
    conn: Connection, parse_id: int, ah_id: int
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM item_transaction_stats
            WHERE parse_id = %s AND ah_id = %s
            LIMIT 1
            """,
            (parse_id, ah_id),
        )
        return cur.fetchone() is not None


def insert_item_transaction_stats(
    conn: Connection,
    rows: Sequence[tuple[int, int, int, int, int, int, int]],
) -> None:
    """
    rows: (parse_id, ah_id, item_id, low, high, median, tx_count)
    low/high/median = unit copper; tx_count = sold listings.
    """
    if not rows:
        return
    by_parse: dict[int, list[tuple]] = {}
    for row in rows:
        by_parse.setdefault(int(row[0]), []).append(row)
    sql = (
        "INSERT INTO item_transaction_stats "
        "(parse_id, ah_id, item_id, low, high, median, tx_count, parsed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ah_id, item_id, parse_id, parsed_at) DO UPDATE SET "
        "low = EXCLUDED.low, high = EXCLUDED.high, "
        "median = EXCLUDED.median, tx_count = EXCLUDED.tx_count"
    )
    with conn.cursor() as cur:
        for parse_id, group in by_parse.items():
            parsed_at = _parsed_at_for(conn, parse_id)
            payload = [(*row, parsed_at) for row in group]
            for batch in _batched(payload):
                cur.executemany(sql, batch)
    conn.commit()
