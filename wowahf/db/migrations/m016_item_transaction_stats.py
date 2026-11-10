VERSION = "016_item_transaction_stats"

COMPRESS_AFTER = "3 days"


def upgrade(conn) -> None:
    """
    Per-parse item sold stats (low/high/median unit copper + tx_count).
    Replaces trans_candle.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT remove_compression_policy(
                'public.trans_candle', if_exists => TRUE
            )
            """
        )
        cur.execute("DROP TABLE IF EXISTS public.trans_candle CASCADE")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS item_transaction_stats (
                parsed_at TIMESTAMPTZ NOT NULL,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                low BIGINT NOT NULL,
                high BIGINT NOT NULL,
                median BIGINT NOT NULL,
                tx_count BIGINT NOT NULL
            )
            """
        )
        cur.execute(
            """
            SELECT create_hypertable(
                'public.item_transaction_stats',
                by_range('parsed_at'),
                migrate_data => TRUE,
                if_not_exists => TRUE
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_item_transaction_stats_ht
            ON public.item_transaction_stats
                (ah_id, item_id, parse_id, parsed_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_tx_stats_parse_ah
            ON public.item_transaction_stats (parse_id, ah_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_tx_stats_item_ah
            ON public.item_transaction_stats
                (item_id, ah_id, parsed_at DESC)
            """
        )
        cur.execute(
            f"""
            ALTER TABLE public.item_transaction_stats SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'ah_id, item_id',
                timescaledb.compress_orderby = 'parsed_at DESC, parse_id DESC'
            )
            """
        )
        cur.execute(
            f"""
            SELECT add_compression_policy(
                'public.item_transaction_stats',
                compress_after => INTERVAL '{COMPRESS_AFTER}',
                if_not_exists => TRUE
            )
            """
        )
