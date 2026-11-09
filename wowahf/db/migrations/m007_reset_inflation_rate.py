VERSION = "007_reset_inflation_rate"


def upgrade(conn) -> None:
    """
    Drop stored inflation_rate rows so stats recalculates with the
    volume-weighted formula (median was stuck at 0.00).
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM market_stats WHERE metric = %s",
            ("inflation_rate",),
        )
