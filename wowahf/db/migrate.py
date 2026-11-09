from datetime import datetime, timezone

from pymysql.connections import Connection

from wowahf.db.migrations import ALL


def _ensure_migrations_table(conn: Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(64) NOT NULL PRIMARY KEY,
                applied_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )


def _applied_versions(conn: Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def migrate(conn: Connection) -> list[str]:
    """Apply pending migrations in order. Returns applied version names."""
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    newly: list[str] = []

    for module in ALL:
        version = module.VERSION
        if version in applied:
            continue
        print(f"db: applying migration {version}")
        module.upgrade(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                (version, datetime.now(timezone.utc).replace(tzinfo=None)),
            )
        conn.commit()
        newly.append(version)

    if not newly:
        print("db: schema up to date")
    return newly
