VERSION = "032_commodity_market_activity"

COMPRESS_AFTER = "3 days"


def upgrade(conn) -> None:
    """
    Replace item_market_activity with commodity_market_activity (commodities only).
    Stats job rebuilds the full history after this migration.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT remove_compression_policy(
                'public.item_market_activity', if_exists => TRUE
            )
            """
        )
        cur.execute("DROP TABLE IF EXISTS public.item_market_activity CASCADE")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS commodity_market_activity (
                parsed_at TIMESTAMPTZ NOT NULL,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                median_pct NUMERIC(20, 2) NOT NULL,
                hotness_pct NUMERIC(20, 2) NOT NULL
            )
            """
        )
        cur.execute(
            """
            SELECT create_hypertable(
                'public.commodity_market_activity',
                by_range('parsed_at'),
                migrate_data => TRUE,
                if_not_exists => TRUE
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_commodity_market_activity_ht
            ON public.commodity_market_activity
                (ah_id, item_id, parse_id, parsed_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_commodity_market_activity_parse_ah
            ON public.commodity_market_activity (parse_id, ah_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_commodity_market_activity_item_ah
            ON public.commodity_market_activity
                (item_id, ah_id, parsed_at DESC)
            """
        )
        cur.execute(
            """
            ALTER TABLE public.commodity_market_activity SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'ah_id, item_id',
                timescaledb.compress_orderby = 'parsed_at DESC, parse_id DESC'
            )
            """
        )
        cur.execute(
            f"""
            SELECT add_compression_policy(
                'public.commodity_market_activity',
                compress_after => INTERVAL '{COMPRESS_AFTER}',
                if_not_exists => TRUE
            )
            """
        )
