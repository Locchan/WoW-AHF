"""On-demand per-house table DDL. Shape changes after v1 go through migrations."""

from pymysql.connections import Connection

TIME_LEFT_ENUM = "'SHORT','MEDIUM','LONG','VERY_LONG'"


def raw_table(house_id: int) -> str:
    return f"ah_raw_{house_id}"


def aggr_table(house_id: int) -> str:
    return f"ah_aggr_{house_id}"


def trans_table(house_id: int) -> str:
    return f"ah_trans_{house_id}"


def watch_table(house_id: int) -> str:
    return f"ah_watch_{house_id}"


def ensure_house_tables(conn: Connection, house_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{raw_table(house_id)}` (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                auc_id BIGINT NOT NULL,
                parse_id BIGINT NOT NULL,
                item_id INT NOT NULL,
                price BIGINT NOT NULL,
                auc_amount INT NOT NULL,
                auc_time_left ENUM({TIME_LEFT_ENUM}) NOT NULL,
                INDEX idx_raw_parse (parse_id),
                INDEX idx_raw_auc_parse (auc_id, parse_id),
                INDEX idx_raw_parse_item_price (parse_id, item_id, price)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{aggr_table(house_id)}` (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                item_id INT NOT NULL,
                parse_id BIGINT NOT NULL,
                price BIGINT NOT NULL,
                amount INT NOT NULL,
                INDEX idx_aggr_parse (parse_id),
                INDEX idx_aggr_parse_item (parse_id, item_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{trans_table(house_id)}` (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                item_id INT NOT NULL,
                parse_id BIGINT NOT NULL,
                price BIGINT NOT NULL,
                amount INT NOT NULL,
                INDEX idx_trans_parse (parse_id),
                INDEX idx_trans_parse_item (parse_id, item_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{watch_table(house_id)}` (
                auc_id BIGINT NOT NULL PRIMARY KEY,
                first_time_left ENUM({TIME_LEFT_ENUM}) NOT NULL,
                short_seen_at DATETIME(6) NULL,
                active TINYINT(1) NOT NULL DEFAULT 1,
                INDEX idx_watch_active (active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    conn.commit()
