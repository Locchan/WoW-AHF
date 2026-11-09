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
    """Update parses.parsed_at (used to stamp completion time for scheduling)."""
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


def insert_raw_rows(
    conn: Connection,
    house_id: int,
    rows: Sequence[tuple[int, int, int, int, int, str]],
) -> None:
    """rows: (auc_id, parse_id, item_id, price, auc_amount, auc_time_left)"""
    ensure_house_tables(conn, house_id)
    sql = (
        f"INSERT INTO `{raw_table(house_id)}` "
        "(auc_id, parse_id, item_id, price, auc_amount, auc_time_left) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
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
    rows: Sequence[tuple[int, int, int, int]],
) -> None:
    """rows: (item_id, parse_id, price, amount)"""
    if not rows:
        return
    ensure_house_tables(conn, house_id)
    sql = (
        f"INSERT INTO `{trans_table(house_id)}` "
        "(item_id, parse_id, price, amount) VALUES (%s, %s, %s, %s)"
    )
    with conn.cursor() as cur:
        for batch in _batched(rows):
            cur.executemany(sql, batch)
    conn.commit()


def load_raw_for_parse(
    conn: Connection, house_id: int, parse_id: int
) -> list[tuple[int, int, int, int, str]]:
    """Return (auc_id, item_id, price, auc_amount, auc_time_left)."""
    ensure_house_tables(conn, house_id)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT auc_id, item_id, price, auc_amount, auc_time_left "
            f"FROM `{raw_table(house_id)}` WHERE parse_id = %s",
            (parse_id,),
        )
        return list(cur.fetchall())


def load_watch(
    conn: Connection, house_id: int
) -> dict[int, tuple[str, datetime | None, bool]]:
    """auc_id -> (first_time_left, short_seen_at, active)."""
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
    """
    current_lots: auc_id -> time_left.
    Inserts new watches; sets short_seen_at on first SHORT; marks missing inactive.
    """
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
        first_tl, short_at, active = existing[auc_id]
        if short_at is None and time_left == "SHORT":
            short_updates.append((now_naive, auc_id))
        if not active:
            reactivate.append(auc_id)

    missing = [auc_id for auc_id in existing if auc_id not in current_lots and existing[auc_id][2]]

    with conn.cursor() as cur:
        if inserts:
            cur.executemany(
                f"INSERT INTO `{table}` (auc_id, first_time_left, short_seen_at, active) "
                "VALUES (%s, %s, %s, %s)",
                inserts,
            )
        if short_updates:
            cur.executemany(
                f"UPDATE `{table}` SET short_seen_at = %s WHERE auc_id = %s AND short_seen_at IS NULL",
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
