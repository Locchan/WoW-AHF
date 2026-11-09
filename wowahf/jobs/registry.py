from datetime import datetime, timedelta

from wowahf.db.connection import connect
from wowahf.db.writes import last_parsed_at
from wowahf.items.run import run_item_names
from wowahf.jobs.manager import Job, JobManager
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import run_stats

PARSE_INTERVAL = timedelta(hours=1)
ITEMS_INTERVAL = timedelta(minutes=10)


def _parse_due_at() -> datetime:
    """
    Absolute due time for the next parse.

    Must return last+interval even when that is in the past. Clamping to
    "now" races the scheduler (due is always a few ms ahead of the loop's
    now) and the parse job never fires.
    """
    conn = connect()
    try:
        last = last_parsed_at(conn)
    finally:
        conn.close()
    if last is None:
        return datetime(1970, 1, 1)
    return last + PARSE_INTERVAL


def build_job_manager() -> JobManager:
    mgr = JobManager()
    mgr.register(
        Job(
            name="parse",
            fn=run_parse,
            due_at=_parse_due_at,
            chain=("stats",),
        )
    )
    mgr.register(
        Job(
            name="stats",
            fn=run_stats,
            run_on_startup=True,
        )
    )
    mgr.register(
        Job(
            name="items",
            fn=run_item_names,
            interval=ITEMS_INTERVAL,
            run_on_startup=True,
        )
    )
    return mgr
