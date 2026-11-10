VERSION = "012_trans_candle"

COMPRESS_AFTER = "3 days"


def upgrade(conn) -> None:
    """
    Per-parse transaction high/low candles (one bar = one parse).
    Sparse: only items with sales after outlier cull.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trans_candle (
                parsed_at TIMESTAMPTZ NOT NULL,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                high BIGINT NOT NULL,
                low BIGINT NOT NULL,
                volume BIGINT NOT NULL,
                turnover BIGINT NOT NULL
            )
            """
        )
        cur.execute(
            """
            SELECT create_hypertable(
                'public.trans_candle',
                by_range('parsed_at'),
                migrate_data => TRUE,
                if_not_exists => TRUE
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_trans_candle_ht
            ON public.trans_candle (ah_id, item_id, parse_id, parsed_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trans_candle_parse_ah
            ON public.trans_candle (parse_id, ah_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trans_candle_item_ah
            ON public.trans_candle (item_id, ah_id, parsed_at DESC)
            """
        )
        cur.execute(
            f"""
            ALTER TABLE public.trans_candle SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'ah_id, item_id',
                timescaledb.compress_orderby = 'parsed_at DESC, parse_id DESC'
            )
            """
        )
        cur.execute(
            f"""
            SELECT add_compression_policy(
                'public.trans_candle',
                compress_after => INTERVAL '{COMPRESS_AFTER}',
                if_not_exists => TRUE
            )
            """
        )
