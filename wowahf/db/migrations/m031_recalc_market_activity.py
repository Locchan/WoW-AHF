VERSION = "031_recalc_market_activity"


def upgrade(conn) -> None:
    """
    Clear partial item_market_activity from the slow per-parse backfill so the
    bulk-per-house job can rebuild quickly.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0"
        )
        cur.execute("TRUNCATE public.item_market_activity")
