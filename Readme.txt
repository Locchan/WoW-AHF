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
under wowahf/db/migrations/. Add new schema changes as a new migration
module and register it in wowahf/db/migrations/__init__.py ALL list.

Migration 002 installs MariaDB event purge_ah_raw_daily (procedure
purge_old_ah_raw) which once per day deletes ah_raw_* rows whose parse is
older than 24 hours. Requires event_scheduler=ON and:
    GRANT EVENT ON `wow_ahf`.* TO 'wowahf'@'%';
    FLUSH PRIVILEGES;

Tables:
    - schema_migrations — applied migration versions
    - parses — id, parsed_at
    - auction_houses — id, name
    - items — id, name, is_commodity, name_status (pending/ok/missing)
    - market_stats — parse_id, ah_id, metric, item_id, value
    - market_price — parse_id, ah_id, item_id, price (unit copper)
    - ah_raw_{houseId} — …, is_commodity (denormalized from items)
    - ah_aggr_{houseId} — id, item_id, parse_id, price, amount
    - ah_trans_{houseId} — …, is_commodity
    - ah_watch_{houseId} — auc_id, first_time_left, short_seen_at, active

Item names come from static-forever /data/wow/item/{id}. Many AH ids 404;
those are stored as name_status=missing and not retried every run.
is_commodity is learned from which AH feed an item_id appears on, stored
in items, and copied onto raw rows for only the item_ids seen this parse.

price is buyout for normal auctions and unit_price for commodities.
Bid-only auctions (no buyout) are skipped.

main.py runs parse (when due), then always runs stats backfill.

market_stats metrics (per ah_id + parse_id; item_id=0 unless noted):
    - market_value_total — copper to buy out AH (top outliers culled)
    - market_value_trans / turnover_value — inferred sales value
    - turnover_volume — units in inferred sales
    - turnover_rate — turnover_value / market_value_total * 100
    - listing_count — number of lots
    - volume_total — sum of listing quantities
    - unique_items — distinct item ids listed
    - sold_unique_items — distinct item ids in ah_trans
    - inflation_rate — median % change of high-volume items
    - market_depth (per item) — units at <= market_price * 1.05
    - spread (per item) — (high-low)/market_price % inside main cluster

market_price table (per item):
    - price — min of main volume price cluster (copper per unit)

Grafana example:
    SELECT p.parsed_at AS time, mp.price / 10000 AS value
    FROM market_price mp
    JOIN parses p ON p.id = mp.parse_id
    WHERE mp.ah_id = 110 AND mp.item_id = <ITEM>
      AND $__timeFilter(p.parsed_at)
    ORDER BY p.parsed_at;

ah_trans only includes lots whose first seen time_left was LONG or VERY_LONG.
SHORT/MEDIUM still go into raw and aggr. Once a tracked lot is seen as SHORT,
later disappearances are treated as expiry (not sales). Cancelled/taken-down
lots count as sold.

Run:
    poetry install
    poetry run python main.py

main.py loops forever: checks the latest parses.parsed_at, sleeps until
one hour after that (or starts immediately if overdue / no prior parse),
runs a parse, then repeats.

Docker:
    docker build -t wow-ahf .
    docker run --rm -v /path/to/config.json:/config.json:ro wow-ahf

Config is read from /config.json when that file exists, else ./config.json.
