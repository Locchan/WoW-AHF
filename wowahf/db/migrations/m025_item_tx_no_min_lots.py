VERSION = "025_item_tx_no_min_lots"


def upgrade(conn) -> None:
    """
    Drop item_transaction_stats for parses that still have ah_raw so stats
    can refill without the MIN_ITEM_LISTINGS floor (per-item tx charts).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT substring(tablename from 'ah_raw_([0-9]+)$')::int,
                   tablename
            FROM pg_tables
            WHERE schemaname = 'data_raw'
              AND tablename ~ '^ah_raw_[0-9]+$'
            ORDER BY 1
            """
        )
        raw_tables = [r[1] for r in cur.fetchall()]
        if not raw_tables:
            return

        parse_ids: set[int] = set()
        for table in raw_tables:
            cur.execute(f"SELECT DISTINCT parse_id FROM data_raw.{table}")
            parse_ids.update(int(r[0]) for r in cur.fetchall())
        if not parse_ids:
            return

        cur.execute(
            "DELETE FROM public.item_transaction_stats WHERE parse_id = ANY(%s)",
            (sorted(parse_ids),),
        )
