VERSION = "014_trans_lots"


def upgrade(conn) -> None:
    """
    Track sold listing counts (auc_ids) on ah_trans and use them as
    trans_candle.volume. Stack-sum moves to units; old volume is copied there.
    Candles are cleared so stats_trans_candle rebuilds with the new meaning.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'data_transactions'
              AND tablename ~ '^ah_trans_[0-9]+$'
            """
        )
        for (table,) in cur.fetchall():
            cur.execute(
                f"""
                ALTER TABLE data_transactions.{table}
                ADD COLUMN IF NOT EXISTS lots INT NOT NULL DEFAULT 1
                """
            )

        cur.execute(
            """
            ALTER TABLE public.trans_candle
            ADD COLUMN IF NOT EXISTS units BIGINT NOT NULL DEFAULT 0
            """
        )
        # Old volume stored stack sizes; preserve under units before rebuild.
        cur.execute(
            """
            UPDATE public.trans_candle
            SET units = volume
            WHERE units = 0 AND volume > 0
            """
        )
        # Force candle rebuild with volume = sold listings.
        cur.execute("TRUNCATE public.trans_candle")
