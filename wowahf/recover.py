"""Startup recovery after an unclean shutdown (OOM, kill, crash)."""

from __future__ import annotations

from wowahf.db.connection import connect
from wowahf.db.writes import (
    delete_job_run,
    list_incomplete_job_run_ids,
    list_incomplete_parse_ids,
    purge_parse_data,
)
from wowahf.utils.log import log


def recover_after_restart() -> None:
    """
    If the newest parse (or any parse) never reached ok=TRUE, purge its data
    so the next parse starts clean. Incomplete job_runs are dropped so the
    job can be retried (stats jobs are idempotent per parse).
    """
    conn = connect()
    try:
        incomplete_parses = list_incomplete_parse_ids(conn)
        for parse_id in incomplete_parses:
            log(f"recover: purging incomplete parse_id={parse_id}")
            purge_parse_data(conn, parse_id)

        for run_id, job_name in list_incomplete_job_run_ids(conn):
            log(
                f"recover: clearing incomplete job_run id={run_id} "
                f"job={job_name}"
            )
            delete_job_run(conn, run_id)
    finally:
        conn.close()
