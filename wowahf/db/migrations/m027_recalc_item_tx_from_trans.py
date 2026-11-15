VERSION = "027_recalc_item_tx_from_trans"


def upgrade(conn) -> None:
    """
    Drop item_transaction_stats for every parse that still has ah_trans rows.

    Raw retention is shorter than transactions; m025/m026 only cleared the
    raw window, leaving older hours stuck on the >=10-lots filter.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'data_transactions'
              AND tablename ~ '^ah_trans_[0-9]+$'
            ORDER BY 1
            """
        )
        trans_tables = [r[0] for r in cur.fetchall()]
        if not trans_tables:
            return

        parse_ids: set[int] = set()
        for table in trans_tables:
            cur.execute(
                f"SELECT DISTINCT parse_id FROM data_transactions.{table}"
            )
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
