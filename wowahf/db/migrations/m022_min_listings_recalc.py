VERSION = "022_min_listings_recalc"

# Snapshot + delta metrics recomputed from ah_raw / ah_trans.
RECALC_METRICS = (
    "market_value_total",
    "listing_count",
    "volume_total",
    "unique_items",
    "market_value_trans",
    "turnover_value",
    "turnover_volume",
    "sold_unique_items",
    "turnover_rate",
    "inflation_rate",
)


def upgrade(conn) -> None:
    """
    Drop market metrics for parses that still have ah_raw so stats jobs can
    refill with MIN_ITEM_LISTINGS=10. Older parses without raw are left alone.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT substring(tablename from 'ah_raw_([0-9]+)$')::int AS ah_id,
                   tablename
            FROM pg_tables
            WHERE schemaname = 'data_raw'
              AND tablename ~ '^ah_raw_[0-9]+$'
            ORDER BY 1
            """
        )
        raw_tables = [(int(r[0]), r[1]) for r in cur.fetchall()]
        if not raw_tables:
            return

        parse_ids: set[int] = set()
        for _ah_id, table in raw_tables:
            cur.execute(
                f"SELECT DISTINCT parse_id FROM data_raw.{table}"
            )
            parse_ids.update(int(r[0]) for r in cur.fetchall())
        if not parse_ids:
            return

        ids = sorted(parse_ids)
        cur.execute(
            """
            DELETE FROM public.market_stats
            WHERE parse_id = ANY(%s) AND metric = ANY(%s)
            """,
            (ids, list(RECALC_METRICS)),
        )
        cur.execute(
            "DELETE FROM public.market_price WHERE parse_id = ANY(%s)",
            (ids,),
        )
        cur.execute(
            "DELETE FROM public.market_depth WHERE parse_id = ANY(%s)",
            (ids,),
        )
        cur.execute(
            "DELETE FROM public.market_spread WHERE parse_id = ANY(%s)",
            (ids,),
        )
        cur.execute(
            "DELETE FROM public.item_transaction_stats WHERE parse_id = ANY(%s)",
            (ids,),
        )
