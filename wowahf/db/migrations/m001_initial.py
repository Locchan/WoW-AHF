VERSION = "001_initial"


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS parses (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                parsed_at DATETIME(6) NOT NULL,
                INDEX idx_parses_parsed_at (parsed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auction_houses (
                id INT NOT NULL PRIMARY KEY,
                name VARCHAR(128) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
