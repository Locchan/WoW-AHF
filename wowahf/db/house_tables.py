"""On-demand per-house table DDL. Shape changes after v1 go through migrations."""

from psycopg import Connection

# Schema layout matches LocInfra Postgres provisioning.
SCHEMA_RAW = "data_raw"
SCHEMA_AGGR = "data_aggregated"
SCHEMA_WATCH = "data_watch"
SCHEMA_TRANS = "data_transactions"

COMPRESS_AFTER = "3 days"


def raw_table(house_id: int) -> str:
    return f"{SCHEMA_RAW}.ah_raw_{house_id}"


def aggr_table(house_id: int) -> str:
    return f"{SCHEMA_AGGR}.ah_aggr_{house_id}"


def trans_table(house_id: int) -> str:
    return f"{SCHEMA_TRANS}.ah_trans_{house_id}"


def watch_table(house_id: int) -> str:
    return f"{SCHEMA_WATCH}.ah_watch_{house_id}"


def _ensure_hypertable(cur, qualified: str, segmentby: str) -> None:
    cur.execute(
        f"""
        SELECT create_hypertable(
            '{qualified}',
            by_range('parsed_at'),
            migrate_data => TRUE,
            if_not_exists => TRUE
        )
        """
    )
    cur.execute(
        f"""
        ALTER TABLE {qualified} SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = '{segmentby}',
            timescaledb.compress_orderby = 'parsed_at DESC, parse_id DESC'
        )
        """
    )
    cur.execute(
        f"""
        SELECT add_compression_policy(
            '{qualified}',
            compress_after => INTERVAL '{COMPRESS_AFTER}',
            if_not_exists => TRUE
        )
        """
    )


def ensure_house_tables(conn: Connection, house_id: int) -> None:
    raw = f"ah_raw_{house_id}"
    aggr = f"ah_aggr_{house_id}"
    trans = f"ah_trans_{house_id}"
    watch = f"ah_watch_{house_id}"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_RAW}.{raw} (
                auc_id BIGINT NOT NULL,
                parse_id BIGINT NOT NULL,
                item_id INT NOT NULL,
                price BIGINT NOT NULL,
                auc_amount INT NOT NULL,
                auc_time_left auc_time_left NOT NULL,
                is_commodity BOOLEAN NOT NULL DEFAULT FALSE,
                PRIMARY KEY (auc_id, parse_id)
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{raw}_parse "
            f"ON {SCHEMA_RAW}.{raw} (parse_id)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{raw}_parse_item_price "
            f"ON {SCHEMA_RAW}.{raw} (parse_id, item_id, price)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{raw}_item "
            f"ON {SCHEMA_RAW}.{raw} (item_id)"
        )

        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_AGGR}.{aggr} (
                item_id INT NOT NULL,
                parse_id BIGINT NOT NULL,
                parsed_at TIMESTAMPTZ NOT NULL,
                price BIGINT NOT NULL,
                amount INT NOT NULL
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{aggr}_parse_item "
            f"ON {SCHEMA_AGGR}.{aggr} (parse_id, item_id)"
        )
        _ensure_hypertable(cur, f"{SCHEMA_AGGR}.{aggr}", "item_id")

        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_TRANS}.{trans} (
                item_id INT NOT NULL,
                parse_id BIGINT NOT NULL,
                parsed_at TIMESTAMPTZ NOT NULL,
                price BIGINT NOT NULL,
                amount INT NOT NULL,
                is_commodity BOOLEAN NOT NULL DEFAULT FALSE,
                lots INT NOT NULL DEFAULT 1
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{trans}_parse_item "
            f"ON {SCHEMA_TRANS}.{trans} (parse_id, item_id)"
        )
        _ensure_hypertable(cur, f"{SCHEMA_TRANS}.{trans}", "item_id")

        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_WATCH}.{watch} (
                auc_id BIGINT PRIMARY KEY,
                first_time_left auc_time_left NOT NULL,
                short_seen_at TIMESTAMPTZ NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{watch}_active "
            f"ON {SCHEMA_WATCH}.{watch} (active)"
        )
    conn.commit()
