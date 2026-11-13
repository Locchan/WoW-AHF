"""Startup recovery after an unclean shutdown (OOM, kill, crash)."""

from __future__ import annotations

from datetime import UTC, datetime

from wowahf.db.connection import connect
from wowahf.db.writes import (
    delete_job_run,
    list_incomplete_job_run_ids,
    list_incomplete_parse_ids,
    purge_parse_data,
    purge_parse_rows,
)
from wowahf.pipeline.ingest import ingest_snapshot, invalidate_metrics_from
from wowahf.pipeline.snapshot import delete_snapshot, snapshot_exists
from wowahf.utils.log import log


def recover_after_restart() -> None:
    """
    Incomplete parses with a gzip snapshot are re-ingested (parse id kept).
    Incomplete parses without a snapshot are removed. Incomplete job_runs are
    dropped so jobs can retry. Recovered parses invalidate later delta metrics.
    """
    conn = connect()
    now = datetime.now(UTC)
    try:
        incomplete_parses = list_incomplete_parse_ids(conn)
        for parse_id in incomplete_parses:
            if snapshot_exists(parse_id):
                log(f"recover: re-ingesting parse_id={parse_id} from gzip snapshot")
                try:
                    conn.rollback()
                    purge_parse_rows(conn, parse_id)
                    ingest_snapshot(conn, parse_id, now=now)
                    invalidate_metrics_from(conn, parse_id)
                except Exception as exc:
                    log(f"recover: ingest failed parse_id={parse_id}: {exc}")
                    try:
                        conn.rollback()
                        purge_parse_rows(conn, parse_id)
                    except Exception:
                        pass
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
