VERSION = "008_cull_watch_event"


def upgrade(conn) -> None:
    """Legacy MariaDB EVENT migration; replaced by pg_cron in 010_pg_baseline."""
    return
