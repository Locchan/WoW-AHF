VERSION = "017_drop_dup_parses"


def upgrade(conn) -> None:
    """
    Remove parses that share a Last-Modified with an earlier parse_id
    (scheduler spin when Blizzard LM had not advanced). Keep the first
    parse per parsed_at and delete orphaned house / market rows.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM parses p
            WHERE EXISTS (
                SELECT 1 FROM parses older
                WHERE older.parsed_at = p.parsed_at
                  AND older.id < p.id
            )
            ORDER BY id
            """
        )
        dup_ids = [int(r[0]) for r in cur.fetchall()]
        if not dup_ids:
            return

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

        for parse_id in dup_ids:
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
