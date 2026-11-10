"""Ordered schema migrations. Add new modules and register them in ALL."""

from wowahf.db.migrations import (
    m001_initial,
    m002_purge_raw_event,
    m003_market_stats,
    m004_market_price_table,
    m005_depth_spread_tables,
    m006_recreate_market_stats,
    m007_reset_inflation_rate,
    m008_cull_watch_event,
    m009_event_run_log,
    m010_pg_baseline,
    m011_space_optimize,
    m012_trans_candle,
    m013_raw_keep3,
    m014_trans_lots,
    m015_raw_keep_25,
)

ALL = [
    m001_initial,
    m002_purge_raw_event,
    m003_market_stats,
    m004_market_price_table,
    m005_depth_spread_tables,
    m006_recreate_market_stats,
    m007_reset_inflation_rate,
    m008_cull_watch_event,
    m009_event_run_log,
    m010_pg_baseline,
    m011_space_optimize,
    m012_trans_candle,
    m013_raw_keep3,
    m014_trans_lots,
    m015_raw_keep_25,
]
