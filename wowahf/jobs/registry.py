from datetime import datetime, timedelta

from wowahf.db.connection import connect
from wowahf.db.writes import last_parsed_at
from wowahf.events.run import run_event_health
from wowahf.items.run import run_item_names
from wowahf.jobs.manager import Job, JobManager
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import run_stats
from wowahf.utils.http_date import naive_utc_second

PARSE_INTERVAL = timedelta(hours=1)
ITEMS_INTERVAL = timedelta(minutes=10)
EVENT_HEALTH_INTERVAL = timedelta(minutes=10)


def _parse_due_at() -> datetime:
    """
    Next parse = last parses.parsed_at (Blizzard Last-Modified) + 1h.

    On startup this aligns to the stored LM; after each parse, touch_parse
    writes the new LM so the following due shifts accordingly.
    """
    conn = connect()
    try:
        last = last_parsed_at(conn)
    finally:
        conn.close()
    if last is None:
        return datetime(1970, 1, 1)
    return naive_utc_second(last) + PARSE_INTERVAL


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
    mgr.register(
        Job(
            name="event_health",
            fn=run_event_health,
            interval=EVENT_HEALTH_INTERVAL,
            run_on_startup=True,
        )
    )
    return mgr
