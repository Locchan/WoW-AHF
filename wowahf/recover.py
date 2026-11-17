"""Startup recovery after an unclean shutdown (OOM, kill, crash)."""

from __future__ import annotations

from datetime import UTC, datetime

from wowahf.db.connection import connect
from wowahf.db.writes import (
    delete_job_run,
    list_incomplete_job_run_ids,
    list_incomplete_parse_ids,
    purge_parse_data,
)
from wowahf.pipeline.import_snapshots import (
    import_missing_snapshots,
    reingest_missed_snapshots,
)
from wowahf.pipeline.snapshot import delete_snapshot, snapshot_exists
from wowahf.utils.log import log


def recover_after_restart() -> None:
    """
    Import every on-disk snapshot missing from the DB, re-ingest incomplete
    ok parses (partial house coverage after deadlocks), purge incomplete parse
    rows that have no snapshot, and clear incomplete job_runs.
    """
    conn = connect()
    now = datetime.now(UTC)
    try:
        try:
            imported = import_missing_snapshots(conn, now=now)
            if imported:
                log(f"recover: imported parse_ids={imported}")
        except Exception as exc:
            log(f"recover: snapshot import failed: {exc}")

        try:
            reingested = reingest_missed_snapshots(conn, now=now)
            if reingested:
                log(f"recover: reingested missed parse_ids={reingested}")
        except Exception as exc:
            log(f"recover: missed reingest failed: {exc}")

        for parse_id in list_incomplete_parse_ids(conn):
            if snapshot_exists(parse_id):
                # import_missing / reingest should have fixed these.
                continue
            log(
                f"recover: purging incomplete parse_id={parse_id} "
                "(no snapshot on disk)"
            )
            try:
                conn.rollback()
                purge_parse_data(conn, parse_id)
                delete_snapshot(parse_id)
            except Exception as exc:
                log(f"recover: purge failed parse_id={parse_id}: {exc}")

        for run_id, job_name in list_incomplete_job_run_ids(conn):
            log(
                f"recover: clearing incomplete job_run id={run_id} "
                f"job={job_name}"
            )
            delete_job_run(conn, run_id)
    finally:
        conn.close()
