VERSION = "005_depth_spread_tables"


def upgrade(conn) -> None:
    """
    Split per-item depth/spread out of market_stats, then drop item_id
    from market_stats (only house-level metrics remain there).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_depth (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                depth BIGINT NOT NULL,
                UNIQUE KEY uq_market_depth (parse_id, ah_id, item_id),
                INDEX idx_md_parse_ah (parse_id, ah_id),
                INDEX idx_md_item_parse (item_id, parse_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_spread (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                spread DECIMAL(20, 2) NOT NULL,
                UNIQUE KEY uq_market_spread (parse_id, ah_id, item_id),
                INDEX idx_mspread_parse_ah (parse_id, ah_id),
                INDEX idx_mspread_item_parse (item_id, parse_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        cur.execute(
            """
            INSERT IGNORE INTO market_depth (parse_id, ah_id, item_id, depth)
            SELECT parse_id, ah_id, item_id, CAST(value AS SIGNED)
            FROM market_stats
            WHERE metric = 'market_depth'
              AND item_id <> 0
            """
        )
        cur.execute(
            """
            INSERT IGNORE INTO market_spread (parse_id, ah_id, item_id, spread)
            SELECT parse_id, ah_id, item_id, value
            FROM market_stats
            WHERE metric = 'spread'
              AND item_id <> 0
            """
        )

        cur.execute(
            """
            DELETE FROM market_stats
            WHERE metric IN ('market_depth', 'spread', 'market_price')
            """
        )

        # item_id is unused for remaining house-level metrics
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'market_stats'
              AND column_name = 'item_id'
            """
        )
        if cur.fetchone()[0]:
            # Drop unique key that includes item_id before dropping the column.
            cur.execute(
                """
                SELECT INDEX_NAME
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'market_stats'
                  AND column_name = 'item_id'
                """
            )
            index_names = {row[0] for row in cur.fetchall()}
            for name in index_names:
                if name == "PRIMARY":
                    continue
                cur.execute(f"ALTER TABLE market_stats DROP INDEX `{name}`")

            cur.execute("ALTER TABLE market_stats DROP COLUMN item_id")
            cur.execute(
                """
                ALTER TABLE market_stats
                ADD UNIQUE KEY uq_ms_row (parse_id, ah_id, metric)
                """
            )
