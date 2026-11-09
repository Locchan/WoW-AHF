"""Ordered schema migrations. Add new modules and register them in ALL."""

from wowahf.db.migrations import (
    m001_initial,
    m002_purge_raw_event,
    m003_market_stats,
    m004_market_price_table,
)

ALL = [
    m001_initial,
    m002_purge_raw_event,
    m003_market_stats,
    m004_market_price_table,
]
