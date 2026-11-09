"""Ordered schema migrations. Add new modules and register them in ALL."""

from wowahf.db.migrations import (
    m001_initial,
    m002_purge_raw_event,
    m003_market_stats,
    m004_market_price_table,
    m005_depth_spread_tables,
    m006_recreate_market_stats,
)

ALL = [
    m001_initial,
    m002_purge_raw_event,
    m003_market_stats,
    m004_market_price_table,
    m005_depth_spread_tables,
    m006_recreate_market_stats,
]
