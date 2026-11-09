from datetime import UTC, datetime

from psycopg import Connection

from wowahf.db.migrations import ALL
from wowahf.utils.log import log


def _ensure_migrations_table(conn: Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(64) NOT NULL PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL
            )
            """
        )


def _applied_versions(conn: Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def migrate(conn: Connection) -> list[str]:
    """Apply pending migrations in order. Returns applied version names."""
    log("db: migrations started")
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    newly: list[str] = []
    skipped = 0

    for module in ALL:
        version = module.VERSION
        if version in applied:
            skipped += 1
            continue
        log(f"db: applying migration {version}")
        module.upgrade(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                (version, datetime.now(UTC)),
            )
        conn.commit()
        newly.append(version)
        log(f"db: migration {version} done")

    if newly:
        log(
            f"db: migrations finished "
            f"applied={len(newly)} skipped={skipped} total={len(ALL)} "
            f"new=[{', '.join(newly)}]"
        )
    else:
        log(
            f"db: migrations finished "
            f"applied=0 skipped={skipped} total={len(ALL)} (up to date)"
        )
    return newly
