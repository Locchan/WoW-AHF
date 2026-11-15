VERSION = "024_fill_metric_gaps"

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
    1) Drop inflation_rate so stats can refill with the lower volume floor.
    2) Insert 0 for delta metrics on multi-hour LM gap parses (Grafana holes).
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.market_stats WHERE metric = 'inflation_rate'"
        )

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

        cur.execute("SELECT id FROM auction_houses ORDER BY id")
        house_ids = [int(r[0]) for r in cur.fetchall()]

        for parse_id in gap_ids:
            cur.execute(
                "SELECT parsed_at FROM parses WHERE id = %s",
                (parse_id,),
            )
            row = cur.fetchone()
            if not row:
                continue
            parsed_at = row[0]
            for ah_id in house_ids:
                for metric in DELTA_METRICS:
                    cur.execute(
                        """
                        INSERT INTO public.market_stats
                            (parse_id, ah_id, metric, value, parsed_at)
                        SELECT %s, %s, %s, 0, %s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM public.market_stats
                            WHERE parse_id = %s AND ah_id = %s AND metric = %s
                        )
                        """,
                        (
                            parse_id,
                            ah_id,
                            metric,
                            parsed_at,
                            parse_id,
                            ah_id,
                            metric,
                        ),
                    )
