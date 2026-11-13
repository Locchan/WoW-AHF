from datetime import UTC, datetime

from wowahf.blizzard.ah_fetcher import iter_ah_houses, peek_ah_last_modified
from wowahf.db.connection import connect
from wowahf.db.writes import (
    create_parse,
    find_incomplete_parse_for_lm,
    last_parsed_at,
    purge_parse_rows,
)
from wowahf.pipeline.ingest import ingest_snapshot
from wowahf.pipeline.schedule import PARSE_INTERVAL, mark_parse_attempt
from wowahf.pipeline.snapshot import (
    cleanup_old_snapshots,
    delete_snapshot,
    snapshot_exists,
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


def run_parse() -> int | bool:
    """
    Download AH data to a gzip snapshot under /mounted/parses, then ingest.

    parses.parsed_at is Blizzard Last-Modified. ok stays FALSE until ingest
    finishes. On failure the parse id and gzip snapshot (if complete) are kept
    so recovery can finish without re-fetching.
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

    conn = connect()
    parse_id: int | None = None
    snapshot_ready = False
    try:
        last_lm = last_parsed_at(conn)
        if last_lm is not None and snapshot_at <= naive_utc_second(last_lm):
            log(
                f"parse: snapshot unchanged "
                f"(LM={snapshot_at.isoformat()}), skip"
            )
            return False

        # Reuse a reserved id when retrying the same LM after a failed ingest.
        parse_id = find_incomplete_parse_for_lm(conn, snapshot_at)
        if parse_id is None:
            parse_id = create_parse(conn, snapshot_at)
            log(f"parse: reserved parse_id={parse_id} lm={snapshot_at.isoformat()}")
        else:
            log(
                f"parse: reusing incomplete parse_id={parse_id} "
                f"lm={snapshot_at.isoformat()}"
            )

        if not snapshot_exists(parse_id):
            log(f"parse: downloading snapshot for parse_id={parse_id}")
            write_snapshot(parse_id, snapshot_at, _download_houses())
        else:
            log(f"parse: using existing gzip snapshot parse_id={parse_id}")
        snapshot_ready = True

        processed = ingest_snapshot(conn, parse_id, now=now)
        next_due = snapshot_at + PARSE_INTERVAL
        log(
            f"parse: finished parse_id={parse_id} "
            f"houses={processed} "
            f"last_modified={snapshot_at.isoformat()} "
            f"next_due={next_due.isoformat()}"
        )
        return parse_id
    except Exception:
        if parse_id is not None:
            try:
                conn.rollback()
                log(
                    f"parse: failed; clearing table rows for parse_id={parse_id} "
                    "(parse id kept)"
                )
                purge_parse_rows(conn, parse_id)
            except Exception as purge_exc:
                log(f"parse: row cleanup after failure failed: {purge_exc}")
            if not snapshot_ready:
                # Incomplete download — drop partial gzip so retry re-fetches.
                delete_snapshot(parse_id)
            else:
                cleanup_old_snapshots()
        raise
    finally:
        conn.close()
