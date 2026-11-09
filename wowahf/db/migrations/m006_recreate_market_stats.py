VERSION = "006_recreate_market_stats"


def upgrade(conn) -> None:
    """
    Recreate market_stats so AUTO_INCREMENT ids are dense again after
    the large deletes from splitting out per-item metrics.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE market_stats_new (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                metric VARCHAR(64) NOT NULL,
                value DECIMAL(20, 2) NOT NULL,
                INDEX idx_ms_parse_ah_metric (parse_id, ah_id, metric),
                UNIQUE KEY uq_ms_row (parse_id, ah_id, metric)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # Support both pre- and post-005 schemas during copy.
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'market_stats'
              AND column_name = 'item_id'
            """
        )
        has_item_id = cur.fetchone()[0] > 0

        if has_item_id:
            cur.execute(
                """
                INSERT INTO market_stats_new (parse_id, ah_id, metric, value)
                SELECT parse_id, ah_id, metric, value
                FROM market_stats
                WHERE item_id = 0
                   OR metric NOT IN ('market_price', 'market_depth', 'spread')
                ORDER BY parse_id, ah_id, metric
                """
            )
        else:
            cur.execute(
                """
                INSERT INTO market_stats_new (parse_id, ah_id, metric, value)
                SELECT parse_id, ah_id, metric, value
                FROM market_stats
                ORDER BY parse_id, ah_id, metric
                """
            )

        cur.execute("DROP TABLE market_stats")
        cur.execute("RENAME TABLE market_stats_new TO market_stats")
