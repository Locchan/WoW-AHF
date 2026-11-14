VERSION = "023_drop_orphan_mv_spikes"

# Copper thresholds: clearly troll/junk books left after raw purge.
TOTAL_SPIKE = 5_000_000_000  # 500k gold
TRANS_SPIKE = 500_000_000  # 50k gold

SPIKE_METRICS = (
    "market_value_total",
    "market_value_trans",
    "turnover_value",
)


def upgrade(conn) -> None:
    """
    Drop inflated market-value rows for (parse, house) pairs with no ah_raw
    left — those cannot be recomputed under MIN_ITEM_LISTINGS.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'data_raw'
              AND tablename ~ '^ah_raw_[0-9]+$'
            """
        )
        raw_tables = {r[0]: int(r[0].rsplit("_", 1)[1]) for r in cur.fetchall()}

        cur.execute(
            """
            SELECT parse_id, ah_id, metric
            FROM market_stats
            WHERE (metric = 'market_value_total' AND value > %s)
               OR (metric IN ('market_value_trans', 'turnover_value')
                   AND value > %s)
            """,
            (TOTAL_SPIKE, TRANS_SPIKE),
        )
        spikes = [(int(p), int(a), m) for p, a, m in cur.fetchall()]
        if not spikes:
            return

        for parse_id, ah_id, _metric in spikes:
            table = f"ah_raw_{ah_id}"
            if table not in raw_tables:
                has_raw = False
            else:
                cur.execute(
                    f"SELECT EXISTS (SELECT 1 FROM data_raw.{table} "
                    f"WHERE parse_id = %s LIMIT 1)",
                    (parse_id,),
                )
                has_raw = bool(cur.fetchone()[0])
            if has_raw:
                continue
            cur.execute(
                """
                DELETE FROM public.market_stats
                WHERE parse_id = %s AND ah_id = %s AND metric = ANY(%s)
                """,
                (parse_id, ah_id, list(SPIKE_METRICS)),
            )
