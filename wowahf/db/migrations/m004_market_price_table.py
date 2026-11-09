VERSION = "004_market_price_table"


def upgrade(conn) -> None:
    """
    Split per-item market prices out of market_stats into market_price.

    Fields:
      id        — surrogate key
      parse_id  — which AH snapshot
      ah_id     — auction house
      item_id   — item
      price     — unit market price in copper (main-cluster open price)
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_price (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                item_id INT NOT NULL,
                price BIGINT NOT NULL,
                UNIQUE KEY uq_market_price (parse_id, ah_id, item_id),
                INDEX idx_mp_parse_ah (parse_id, ah_id),
                INDEX idx_mp_item_parse (item_id, parse_id),
                INDEX idx_mp_ah_item (ah_id, item_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # Copy existing metric rows (idempotent if re-run after partial apply).
        cur.execute(
            """
            INSERT IGNORE INTO market_price (parse_id, ah_id, item_id, price)
            SELECT parse_id, ah_id, item_id, CAST(value AS SIGNED)
            FROM market_stats
            WHERE metric = 'market_price'
              AND item_id <> 0
            """
        )

        cur.execute(
            """
            DELETE FROM market_stats
            WHERE metric = 'market_price'
            """
        )
