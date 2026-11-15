VERSION = "026_recalc_item_tx"


def upgrade(conn) -> None:
    """
    Re-drop item_transaction_stats for parses with ah_raw.

    m025 cleared once, but the following stats pass still matched the old
    >=10-lots filter counts; force another refill with current crunch code.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'data_raw'
              AND tablename ~ '^ah_raw_[0-9]+$'
            ORDER BY 1
            """
        )
        raw_tables = [r[0] for r in cur.fetchall()]
        if not raw_tables:
            return

        parse_ids: set[int] = set()
        for table in raw_tables:
            cur.execute(f"SELECT DISTINCT parse_id FROM data_raw.{table}")
            parse_ids.update(int(r[0]) for r in cur.fetchall())
        if not parse_ids:
            return

        cur.execute(
            "SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"
        )
        for parse_id in sorted(parse_ids):
            cur.execute(
                "DELETE FROM public.item_transaction_stats WHERE parse_id = %s",
                (parse_id,),
            )
