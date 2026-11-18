from datetime import UTC, datetime
import random
import time

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

# Download-phase backoff when an entire snapshot attempt fails.
_DOWNLOAD_RETRY_CAP_S = 60.0


def _download_retry_sleep(attempt: int, exc: BaseException) -> None:
    delay = min(_DOWNLOAD_RETRY_CAP_S, 1.0 * (2 ** min(attempt, 8)))
    delay *= 0.5 + random.random()
    log(
        f"parse: download attempt failed ({exc.__class__.__name__}: {exc}); "
        f"retry in {delay:.1f}s (attempt {attempt + 1})"
    )
    time.sleep(delay)


def _ensure_snapshot_on_disk() -> datetime | None:
    """
    Peek Blizzard LM (HEAD only) and download to pending gzip until durable.

    Ignores stop_requested — must finish once Blizzard has a new dump.
    Returns the snapshot LM, or None when Blizzard has no auction data.
    """
    attempt = 0
    while True:
        try:
            snapshot_at = peek_ah_last_modified()
            if snapshot_at is None:
                log("parse: Blizzard returned no auction houses — nothing to download")
                return None
            snapshot_at = naive_utc_second(snapshot_at)

            existing = find_snapshot_for_lm(snapshot_at)
            if existing is not None:
                log(
                    f"parse: snapshot already on disk "
                    f"({existing.path.name}) lm={snapshot_at.isoformat()}"
                )
                return snapshot_at

            log(f"parse: downloading snapshot lm={snapshot_at.isoformat()}")
            partial = pending_partial_path(snapshot_at)
            try:
                # Do not abort on SIGTERM mid-house — finish the dump.
                path = write_snapshot(
                    snapshot_at, iter_ah_houses(), parse_id=None
                )
            except Exception:
                delete_snapshot_path(partial)
                raise

            log(
                f"parse: snapshot durable ({path.name}) "
                f"lm={snapshot_at.isoformat()}"
            )
            return snapshot_at
        except Exception as exc:
            # Only .partial was deleted above (if write started). Complete
            # pending/parse gzips are never removed here.
            _download_retry_sleep(attempt, exc)
            attempt += 1


def run_parse() -> int | bool:
    """
    Download a new Blizzard LM to disk first, then import any on-disk
    snapshots missing from the database.

    Download does not need Postgres and does not stop for SIGTERM until the
    gzip is durable. Import does — connection errors propagate so the job
    manager can pause schedules until DB returns.

    Returns the newest imported parse id, or False when there was nothing new
    to ingest (stats chain is skipped).
    """
    log("parse: started")
    mark_parse_attempt()
    now = datetime.now(UTC)

    snapshot_at = _ensure_snapshot_on_disk()
    if snapshot_at is None:
        log("parse: finished (no Blizzard data)")
        return False

    # Snapshot is on disk. Honor stop before touching Postgres.
    if stop_requested():
        log("parse: stop requested after durable snapshot; deferring DB import")
        return False

    conn = connect()
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
