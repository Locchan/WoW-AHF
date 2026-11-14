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
    conn = psycopg.connect(
        _conninfo(),
        row_factory=tuple_row,
        autocommit=False,
        connect_timeout=5,
    )
    with conn.cursor() as cur:
        cur.execute(
            "SET search_path TO public, data_raw, data_aggregated, "
            "data_watch, data_transactions"
        )
    return conn


def ping() -> None:
    """Open a short-lived connection and run SELECT 1. Raises on failure."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        conn.close()


def is_db_unavailable(exc: BaseException) -> bool:
    """True when ``exc`` looks like Postgres/network unavailability."""
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    needles = (
        "could not connect",
        "connection refused",
        "connection reset",
        "server closed the connection",
        "name or service not known",
        "temporary failure in name resolution",
        "failed to resolve host",
        "timeout expired",
        "connection timed out",
        "no route to host",
        "network is unreachable",
        "postgresql",
    )
    return any(n in text for n in needles)
