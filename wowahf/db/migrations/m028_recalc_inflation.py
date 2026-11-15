VERSION = "028_recalc_inflation"


def upgrade(conn) -> None:
    """
    Drop all inflation_rate rows so stats_inflation_rate can rebuild with
    depth>=500 basket and high-only outlier cull (from market_price/depth).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"
        )
        cur.execute(
            "DELETE FROM public.market_stats WHERE metric = 'inflation_rate'"
        )
