from datetime import UTC, datetime

from wowahf.blizzard.ah_fetcher import iter_ah_houses, peek_ah_last_modified
from wowahf.db.connection import connect
from wowahf.pipeline.import_snapshots import import_missing_snapshots
from wowahf.pipeline.schedule import PARSE_INTERVAL, mark_parse_attempt
from wowahf.pipeline.snapshot import (
    delete_snapshot_path,
    find_snapshot_for_lm,
    pending_partial_path,
    write_snapshot,
)
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def _download_houses():
    """Wrap iter_ah_houses so stop mid-download raises cleanly."""
    for house in iter_ah_houses():
        if stop_requested():
            raise RuntimeError("stop requested during Blizzard download")
        yield house


def _try_connect():
    try:
        return connect()
    except Exception as exc:
        log(f"parse: database unavailable: {exc}")
        return None


def run_parse() -> int | bool:
    """
    Always download a new Blizzard LM to disk first (DB optional), then import
    any on-disk snapshots that are missing from the database.

    Returns the newest imported parse id, or False when there was nothing new
    to ingest (stats chain is skipped).
    """
    log("parse: started")
    mark_parse_attempt()
    now = datetime.now(UTC)

    snapshot_at = peek_ah_last_modified()
    if snapshot_at is None:
        snapshot_at = naive_utc_second(now)
        log("parse: warning: no Last-Modified in responses; using wall clock")
    else:
        snapshot_at = naive_utc_second(snapshot_at)

    existing = find_snapshot_for_lm(snapshot_at)
    if existing is None:
        log(f"parse: downloading snapshot lm={snapshot_at.isoformat()}")
        partial = pending_partial_path(snapshot_at)
        try:
            write_snapshot(snapshot_at, _download_houses(), parse_id=None)
        except Exception:
            delete_snapshot_path(partial)
            # Also drop a half-written pending if replace never happened.
            from wowahf.pipeline.snapshot import pending_path

            delete_snapshot_path(pending_path(snapshot_at))
            raise
    else:
        log(
            f"parse: snapshot already on disk "
            f"({existing.path.name}) lm={snapshot_at.isoformat()}"
        )

    conn = _try_connect()
    if conn is None:
        log(
            "parse: snapshot saved; will import when database is back "
            f"(next_due≈{(snapshot_at + PARSE_INTERVAL).isoformat()})"
        )
        return False

    try:
        imported = import_missing_snapshots(conn, now=now)
        if not imported:
            log("parse: all on-disk snapshots already in database")
            return False
        newest = max(imported)
        log(
            f"parse: finished imported={imported} "
            f"last_modified={snapshot_at.isoformat()} "
            f"next_due={(snapshot_at + PARSE_INTERVAL).isoformat()}"
        )
        return newest
    finally:
        conn.close()
