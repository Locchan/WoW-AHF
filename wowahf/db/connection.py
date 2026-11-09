import pymysql
from pymysql.connections import Connection

from wowahf.utils.config import get_config_entry


def _base_kwargs() -> dict:
    return {
        "host": get_config_entry("DB_HOST"),
        "user": get_config_entry("DB_USER"),
        "password": get_config_entry("DB_PASSWORD"),
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": pymysql.cursors.Cursor,
    }


def ensure_database() -> None:
    database = get_config_entry("DB_DATABASE")
    conn = pymysql.connect(**_base_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


def connect() -> Connection:
    ensure_database()
    return pymysql.connect(database=get_config_entry("DB_DATABASE"), **_base_kwargs())
