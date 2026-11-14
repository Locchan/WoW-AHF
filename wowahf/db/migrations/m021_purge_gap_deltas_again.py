VERSION = "021_purge_gap_deltas_again"

# Must match wowahf.pipeline.schedule.PARSE_DELTA_MAX.
DELTA_MAX = "1 hour 30 minutes"

DELTA_METRICS = (
    "market_value_trans",
    "turnover_value",
    "turnover_volume",
    "sold_unique_items",
    "turnover_rate",
    "inflation_rate",
)


def upgrade(conn) -> None:
    """
    Re-purge sale/inflation rows written across LM gaps > one dump.
    m020 ran before later multi-hour gaps (e.g. parse 112) were ingested;
    those still need clearing now that gaps always block sale inference.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id
            FROM (
                SELECT
                    id,
                    parsed_at,
                    lag(parsed_at) OVER (ORDER BY id) AS prev_at
                FROM parses
                WHERE ok
            ) t
            WHERE prev_at IS NOT NULL
              AND parsed_at - prev_at > interval '{DELTA_MAX}'
            ORDER BY id
            """
        )
        gap_ids = [int(r[0]) for r in cur.fetchall()]
        if not gap_ids:
            return

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

        for parse_id in gap_ids:
            for table in trans_tables:
                cur.execute(
                    f"DELETE FROM data_transactions.{table} WHERE parse_id = %s",
                    (parse_id,),
                )
            cur.execute(
                """
                DELETE FROM public.market_stats
                WHERE parse_id = %s AND metric = ANY(%s)
                """,
                (parse_id, list(DELTA_METRICS)),
            )
            cur.execute(
                "DELETE FROM public.item_transaction_stats WHERE parse_id = %s",
                (parse_id,),
            )
