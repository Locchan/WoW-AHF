VERSION = "007_reset_inflation_rate"


def upgrade(conn) -> None:
    """Legacy MariaDB data fix; not applicable after Postgres cutover."""
    return
