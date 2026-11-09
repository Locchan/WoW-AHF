from datetime import datetime
from typing import Sequence

from pymysql.connections import Connection

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
            ON DUPLICATE KEY UPDATE name = VALUES(name)
            """,
            (house_id, name),
        )
    conn.commit()


def _naive_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def create_parse(conn: Connection, parsed_at: datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO parses (parsed_at) VALUES (%s)",
            (_naive_utc(parsed_at),),
        )
        parse_id = cur.lastrowid
    conn.commit()
    return int(parse_id)


def touch_parse(conn: Connection, parse_id: int, parsed_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE parses SET parsed_at = %s WHERE id = %s",
            (_naive_utc(parsed_at), parse_id),
        )
    conn.commit()


def previous_parse_id(conn: Connection, current_parse_id: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM parses WHERE id < %s ORDER BY id DESC LIMIT 1",
            (current_parse_id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def last_parsed_at(conn: Connection) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT parsed_at FROM parses ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    return row[0] if row else None


def list_parse_ids(conn: Connection) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM parses ORDER BY id ASC")
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
        f"INSERT INTO `{raw_table(house_id)}` "
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
            1 if r.is_commodity else 0,
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
    rows = [(i, 1, now_naive) for i in commodity_ids] + [
        (i, 0, now_naive) for i in auction_ids
    ]
    if not rows:
        return
    sql = """
        INSERT INTO items (id, is_commodity, name_status, updated_at)
        VALUES (%s, %s, 'pending', %s)
        ON DUPLICATE KEY UPDATE
            is_commodity = VALUES(is_commodity),
            updated_at = VALUES(updated_at)
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
            fmt = ",".join(["%s"] * len(batch))
            cur.execute(
                f"""
                UPDATE `{table}` AS r
                INNER JOIN items AS i ON i.id = r.item_id
                SET r.is_commodity = i.is_commodity
                WHERE r.item_id IN ({fmt})
                  AND r.is_commodity <> i.is_commodity
                """,
                batch,
            )
            updated += cur.rowcount
    conn.commit()
    return int(updated)


def list_items_pending_names(conn: Connection, limit: int = 200) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM items
            WHERE name_status = 'pending'
            ORDER BY id ASC
            LIMIT %s
            """,
            (limit,),
        )
        return [int(r[0]) for r in cur.fetchall()]


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


def insert_aggr_rows(
    conn: Connection,
    house_id: int,
    rows: Sequence[tuple[int, int, int, int]],
) -> None:
    """rows: (item_id, parse_id, price, amount)"""
    ensure_house_tables(conn, house_id)
    sql = (
        f"INSERT INTO `{aggr_table(house_id)}` "
        "(item_id, parse_id, price, amount) VALUES (%s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()


def insert_trans_rows(
    conn: Connection,
    house_id: int,
    rows: Sequence[tuple[int, int, int, int, int]],
) -> None:
    """rows: (item_id, parse_id, price, amount, is_commodity)"""
    if not rows:
        return
    ensure_house_tables(conn, house_id)
    sql = (
        f"INSERT INTO `{trans_table(house_id)}` "
        "(item_id, parse_id, price, amount, is_commodity) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
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
            f"FROM `{raw_table(house_id)}` WHERE parse_id = %s",
            (parse_id,),
        )
        return list(cur.fetchall())


def load_trans_for_parse(
    conn: Connection, house_id: int, parse_id: int
) -> list[tuple[int, int, int, int]]:
    """(item_id, price, amount, is_commodity)."""
    ensure_house_tables(conn, house_id)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT item_id, price, amount, is_commodity "
            f"FROM `{trans_table(house_id)}` WHERE parse_id = %s",
            (parse_id,),
        )
        return list(cur.fetchall())


def load_watch(
    conn: Connection, house_id: int
) -> dict[int, tuple[str, datetime | None, bool]]:
    ensure_house_tables(conn, house_id)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT auc_id, first_time_left, short_seen_at, active "
            f"FROM `{watch_table(house_id)}`"
        )
        return {
            int(auc_id): (first_tl, short_at, bool(active))
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
            inserts.append((auc_id, time_left, short_at, 1))
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
                f"INSERT INTO `{table}` (auc_id, first_time_left, short_seen_at, active) "
                "VALUES (%s, %s, %s, %s)",
                inserts,
            )
        if short_updates:
            cur.executemany(
                f"UPDATE `{table}` SET short_seen_at = %s "
                "WHERE auc_id = %s AND short_seen_at IS NULL",
                short_updates,
            )
        if reactivate:
            fmt = ",".join(["%s"] * len(reactivate))
            cur.execute(
                f"UPDATE `{table}` SET active = 1 WHERE auc_id IN ({fmt})",
                reactivate,
            )
        if missing:
            fmt = ",".join(["%s"] * len(missing))
            cur.execute(
                f"UPDATE `{table}` SET active = 0 WHERE auc_id IN ({fmt})",
                missing,
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


def insert_market_stats(
    conn: Connection,
    rows: Sequence[tuple[int, int, str, float | int]],
) -> None:
    """rows: (parse_id, ah_id, metric, value) — house-level metrics only."""
    if not rows:
        return
    sql = (
        "INSERT INTO market_stats (parse_id, ah_id, metric, value) "
        "VALUES (%s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()


def insert_market_prices(
    conn: Connection,
    rows: Sequence[tuple[int, int, int, int]],
) -> None:
    """rows: (parse_id, ah_id, item_id, price) — price in copper."""
    if not rows:
        return
    sql = (
        "INSERT INTO market_price (parse_id, ah_id, item_id, price) "
        "VALUES (%s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()


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
    sql = (
        "INSERT INTO market_depth (parse_id, ah_id, item_id, depth) "
        "VALUES (%s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()


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
    sql = (
        "INSERT INTO market_spread (parse_id, ah_id, item_id, spread) "
        "VALUES (%s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()
