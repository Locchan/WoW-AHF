This can be configured with config.json in the root folder or env vars.
Needed variables:

Blizzard:
    - BLIZZARD_CLIENT_ID
    - BLIZZARD_CLIENT_SECRET
    - LOCALE ("en_GB", "en_US", etc.)
    - REGION ("eu", "us", "kr", "tw")
    - RULESET_ID ("1" PvP, "2" Normal, "3" Roleplay, "15" Roleplay PvP)

Uses the WoW: Forever Battle.net API (dynamic-forever-{region}).
Auction houses hang off rulesets, not realms.

Database (PostgreSQL 18 + PostGIS/Timescale/pg_cron via LocInfra):
    - DB_HOST
    - DB_USER
    - DB_PASSWORD
    - DB_DATABASE
    - DB_PORT (optional, default 5432)

On startup the app applies migrations once under wowahf/db/migrations/.
MariaDB-era modules 001–009 are no-ops; 010_pg_baseline is the Postgres
schema; 011_space_optimize drops pgloader duplicate indexes, tightens
types, keeps only the last 2 raw parses, and converts market_*/aggr/trans
to Timescale hypertables with compression (history retained).

House tables live in schemas data_raw / data_aggregated / data_watch /
data_transactions and are created on demand.

Raw purge and watch cull run via pg_cron (scheduled by LocInfra
setup-auth as postgres once the maintenance_* functions exist):
    - purge_ah_raw_daily — 20 * * * * (keeps last 2 parse_ids of ah_raw_*)
    - cull_ah_watch_8h — 20 */8 * * *

Tables:
    - schema_migrations — applied migration versions
    - parses — id, parsed_at (Blizzard AH Last-Modified, not wall clock)
    - auction_houses — id, name
    - items — id, name, is_commodity, name_status (pending/ok/missing)
    - market_stats — parse_id, ah_id, metric, value, parsed_at (hypertable)
    - market_price — parse_id, ah_id, item_id, price, parsed_at (hypertable)
    - market_depth — parse_id, ah_id, item_id, depth, parsed_at (hypertable)
    - market_spread — parse_id, ah_id, item_id, spread, parsed_at (hypertable)
    - item_transaction_stats — per-parse sold low/high/median + tx_count
      (hypertable; unit copper after outlier cull; tx_count = sold listings)
    - ah_raw_{houseId} — PK (auc_id, parse_id); last 25 parses
    - ah_aggr_{houseId} — item_id, parse_id, parsed_at, price, amount (hypertable)
    - ah_trans_{houseId} — …, is_commodity, lots (hypertable)
    - ah_watch_{houseId} — auc_id, first_time_left, short_seen_at, active

Item names come from static-forever /data/wow/item/{id}. A separate items
job every 10 minutes resolves up to 100 pending and 100 previously-missing
ids (oldest updated_at first). 404s stay name_status=missing and are
retried on later passes. is_commodity is learned from which AH feed an
item_id appears on, stored in items, and copied onto raw rows for only the
item_ids seen this parse.

price is buyout for normal auctions and unit_price for commodities.
Bid-only auctions (no buyout) are skipped.

main.py migrates once, then starts a threaded JobManager (wowahf/jobs):
    - parse — due at max(LM + 1h10m, last attempt + 10m). Skips when LM has
      not advanced. Reserves parses.id first, downloads the AH dump to a gzip
      snapshot under /mounted/parses/parse_<id>.json.gz, then ingests from that
      file. On failure the parse id and complete gzip are kept for recovery;
      table rows are cleared. On startup, incomplete parses with a snapshot are
      re-ingested (later delta metrics invalidated); those without a snapshot
      are purged. Keeps at most 48 gzip snapshots. Chains stats on success.
    - stats_* — one job per metric, chained in order (startup + after parse):
        market_value_total → listing_count → volume_total → unique_items →
        backfill_trans → market_value_trans → turnover_volume →
        sold_unique_items → turnover_rate → market_price → market_depth →
        market_spread → inflation_rate → item_transaction
      backfill_trans recomputes missing ah_trans for prior ok parses when
      prev raw is still kept and the failure-gated delta rules allow it.
    - items — on startup and every 10 minutes after each finish
    - event_health — every 10 minutes: pg_cron job presence/active +
      schema_event_runs outcomes / overdue
Jobs run in their own threads; a job already running is not re-spawned.

Watch cull and raw purge run in Postgres (pg_cron), not in the app.
Event SQL failures are logged to schema_event_runs.

market_stats metrics (per ah_id + parse_id; item_id=0 unless noted):
    - market_value_total — copper to buy out AH (top outliers culled)
    - market_value_trans / turnover_value — inferred sales value
    - turnover_volume — units in inferred sales
    - turnover_rate — turnover_value / market_value_total * 100
    - listing_count — number of lots
    - volume_total — sum of listing quantities
    - unique_items — distinct item ids listed
    - sold_unique_items — distinct item ids in ah_trans
    - inflation_rate — volume-weighted mean % change of high-volume items
      (after jump-culling wild per-item % moves); skipped only when an
      unrecoverable incomplete parse sits in an LM gap >1h30m

Per-item tables:
    - market_price.price — min of main volume cluster (copper/unit)
    - market_depth.depth — units at <= market_price * 1.05
    - market_spread.spread — (high-low)/market_price % in main cluster

ah_trans only includes lots whose first seen time_left was LONG or VERY_LONG.
SHORT/MEDIUM still go into raw and aggr. Once a tracked lot is seen as SHORT,
later disappearances are treated as expiry (not sales). Cancelled/taken-down
lots count as sold. Sale inference and turnover / inflation / item_transaction
stats skip a step only when LM gap >1h30m and an incomplete parse without a
gzip snapshot sits in between. Missing ah_trans is recomputed on the stats
chain when previous raw is still retained. Recovered parses invalidate later
delta metrics so jobs recalculate.

Run:
    poetry install
    poetry run python main.py

main.py migrates, starts the job manager, and blocks until SIGTERM/SIGINT.

Docker:
    docker build -t wow-ahf .
    # Mount the host data dir (config.json + parses/) at /mounted
    docker run --rm -v /path/to/wow_ahf:/mounted wow-ahf

Config is read from /config.json when that file exists, else ./config.json.
