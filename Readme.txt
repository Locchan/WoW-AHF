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

Tables:
    - schema_migrations — applied migration versions
    - parses — id, parsed_at
    - auction_houses — id, name
    - ah_raw_{houseId} — id, auc_id, parse_id, item_id, price, auc_amount, auc_time_left
    - ah_aggr_{houseId} — id, item_id, parse_id, price, amount
    - ah_trans_{houseId} — id, item_id, parse_id, price, amount (inferred sales)
    - ah_watch_{houseId} — auc_id, first_time_left, short_seen_at, active

price is buyout for normal auctions and unit_price for commodities.
Bid-only auctions (no buyout) are skipped.

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
