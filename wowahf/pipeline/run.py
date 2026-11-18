"""Import on-disk AH snapshots into the database (no Blizzard download)."""

from datetime import UTC, datetime

from wowahf.db.connection import connect
from wowahf.pipeline.import_snapshots import import_missing_snapshots
from wowahf.pipeline.snapshot import newest_snapshot_lm
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log


def run_parse() -> int | bool:
    """
    Ingest any complete on-disk snapshots that are missing from the DB.

    The ``wow_ahf_fetcher`` container owns Blizzard downloads and publishes
    atomic ``pending_*.json.gz`` files under the shared data dir. This job
    only watches that directory.

    Returns the newest imported parse id, or False when nothing new was
    ingested (stats chain is skipped).
    """
    log("parse: started")
    now = datetime.now(UTC)
    disk_lm = newest_snapshot_lm()
    conn = connect()
    try:
        imported = import_missing_snapshots(conn, now=now)
        if not imported:
            lm_s = (
                naive_utc_second(disk_lm).isoformat() if disk_lm else "none"
            )
            log(f"parse: no new snapshots (newest_disk_lm={lm_s})")
            return False
        newest = max(imported)
        log(f"parse: finished imported={imported}")
        return newest
    finally:
        conn.close()
