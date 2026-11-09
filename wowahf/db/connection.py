import psycopg
from psycopg import Connection
from psycopg.rows import tuple_row

from wowahf import utils
from wowahf.utils.config import get_config_entry


def _optional(name: str, default: str) -> str:
    # Ensure config is loaded without failing on missing keys.
    get_config_entry("DB_HOST")
    cfg = utils.CONFIG_OBJ or {}
    return cfg.get(name, default)


def _conninfo() -> str:
    host = get_config_entry("DB_HOST")
    user = get_config_entry("DB_USER")
    password = get_config_entry("DB_PASSWORD")
    database = get_config_entry("DB_DATABASE")
    port = _optional("DB_PORT", "5432")
    return (
        f"host={host} port={port} dbname={database} "
        f"user={user} password={password}"
    )


def ensure_database() -> None:
    """No-op on Postgres: database is provisioned by LocInfra init."""
    return


def connect() -> Connection:
    conn = psycopg.connect(_conninfo(), row_factory=tuple_row, autocommit=False)
    with conn.cursor() as cur:
        cur.execute(
            "SET search_path TO public, data_raw, data_aggregated, "
            "data_watch, data_transactions"
        )
    return conn
