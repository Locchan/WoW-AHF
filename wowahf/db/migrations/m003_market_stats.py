VERSION = "003_market_stats"


def _alter_house_tables(conn, prefix: str, ddl: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name REGEXP %s
            """,
            (f"^{prefix}[0-9]+$",),
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = %s
                  AND column_name = 'is_commodity'
                """,
                (table,),
            )
            if cur.fetchone()[0]:
                continue
            cur.execute(ddl.format(table=table))


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INT NOT NULL PRIMARY KEY,
                name VARCHAR(255) NULL,
                is_commodity TINYINT(1) NOT NULL DEFAULT 0,
                name_status ENUM('pending','ok','missing') NOT NULL DEFAULT 'pending',
                updated_at DATETIME(6) NOT NULL,
                INDEX idx_items_commodity (is_commodity),
                INDEX idx_items_name_status (name_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_stats (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                parse_id BIGINT NOT NULL,
                ah_id INT NOT NULL,
                metric VARCHAR(64) NOT NULL,
                item_id INT NOT NULL DEFAULT 0,
                value DECIMAL(20, 2) NOT NULL,
                INDEX idx_ms_parse_ah_metric (parse_id, ah_id, metric),
                INDEX idx_ms_metric_item (metric, item_id, parse_id),
                UNIQUE KEY uq_ms_row (parse_id, ah_id, metric, item_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

    _alter_house_tables(
        conn,
        "ah_raw_",
        "ALTER TABLE `{table}` ADD COLUMN is_commodity TINYINT(1) NOT NULL DEFAULT 0 "
        "AFTER auc_time_left, ADD INDEX idx_raw_item (item_id)",
    )
    _alter_house_tables(
        conn,
        "ah_trans_",
        "ALTER TABLE `{table}` ADD COLUMN is_commodity TINYINT(1) NOT NULL DEFAULT 0 "
        "AFTER amount",
    )
