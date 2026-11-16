VERSION = "029_item_median_delta"

COMPRESS_AFTER = "3 days"


def upgrade(conn) -> None:
    """
    Per-item signed % of current median vs tx_count-weighted 5-day avg median.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS item_median_delta (
                parsed_at TIMESTAMPTZ NOT NULL,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                pct NUMERIC(20, 2) NOT NULL
            )
            """
        )
        cur.execute(
            """
            SELECT create_hypertable(
                'public.item_median_delta',
                by_range('parsed_at'),
                migrate_data => TRUE,
                if_not_exists => TRUE
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_item_median_delta_ht
            ON public.item_median_delta
                (ah_id, item_id, parse_id, parsed_at)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_median_delta_parse_ah
            ON public.item_median_delta (parse_id, ah_id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_median_delta_item_ah
            ON public.item_median_delta
                (item_id, ah_id, parsed_at DESC)
            """
        )
        cur.execute(
            """
            ALTER TABLE public.item_median_delta SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'ah_id, item_id',
                timescaledb.compress_orderby = 'parsed_at DESC, parse_id DESC'
            )
            """
        )
        cur.execute(
            f"""
            SELECT add_compression_policy(
                'public.item_median_delta',
                compress_after => INTERVAL '{COMPRESS_AFTER}',
                if_not_exists => TRUE
            )
            """
        )
