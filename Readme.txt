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

Database:
    - DB_HOST
    - DB_USER
    - DB_PASSWORD
    - DB_DATABASE

On startup the app creates the database if missing and applies migrations
once (only at process start) under wowahf/db/migrations/. Add new schema
changes as a new migration module and register it in
wowahf/db/migrations/__init__.py ALL list.

Migration 002 installs MariaDB event purge_ah_raw_daily (procedure
purge_old_ah_raw) which once per day deletes ah_raw_* rows whose parse is
older than 24 hours. Migration 008 installs cull_ah_watch_8h (procedure
cull_inactive_ah_watch) every 8 hours to DELETE active=0 from ah_watch_*.
Requires event_scheduler=ON and:
    GRANT EVENT ON `wow_ahf`.* TO 'wowahf'@'%';
    FLUSH PRIVILEGES;

Tables:
    - schema_migrations — applied migration versions
    - parses — id, parsed_at (Blizzard AH Last-Modified, not wall clock)
    - auction_houses — id, name
    - items — id, name, is_commodity, name_status (pending/ok/missing)
    - market_stats — parse_id, ah_id, metric, value (house-level only)
    - market_price — parse_id, ah_id, item_id, price (unit copper)
    - market_depth — parse_id, ah_id, item_id, depth (units)
    - market_spread — parse_id, ah_id, item_id, spread (% in main cluster)
    - ah_raw_{houseId} — …, is_commodity (denormalized from items)
    - ah_aggr_{houseId} — id, item_id, parse_id, price, amount
    - ah_trans_{houseId} — …, is_commodity
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
    - parse — every 10 minutes: HEAD houses for Last-Modified; full fetch
      only if it differs from parses.parsed_at; chains stats on real runs
    - stats — on startup and after each successful parse (not on skip)
    - items — on startup and every 10 minutes after each finish
    - event_health — every 10 minutes: MariaDB event_scheduler / ENABLED /
      overdue LAST_EXECUTED / last schema_event_runs failure
Jobs run in their own threads; a job already running is not re-spawned.
Watch cull and raw purge run in MariaDB (events), not in the app.
Event SQL failures are logged to schema_event_runs (migration 009).

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
      (after jump-culling wild per-item % moves)

Per-item tables:
    - market_price.price — min of main volume cluster (copper/unit)
    - market_depth.depth — units at <= market_price * 1.05
    - market_spread.spread — (high-low)/market_price % in main cluster

ah_trans only includes lots whose first seen time_left was LONG or VERY_LONG.
SHORT/MEDIUM still go into raw and aggr. Once a tracked lot is seen as SHORT,
later disappearances are treated as expiry (not sales). Cancelled/taken-down
lots count as sold.

Run:
    poetry install
    poetry run python main.py

main.py migrates, starts the job manager, and blocks until SIGTERM/SIGINT.

Docker:
    docker build -t wow-ahf .
    docker run --rm -v /path/to/config.json:/config.json:ro wow-ahf

Config is read from /config.json when that file exists, else ./config.json.
