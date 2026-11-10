from datetime import datetime, timedelta

from wowahf.db.connection import connect
from wowahf.db.writes import last_parsed_at
from wowahf.events.run import run_event_health
from wowahf.items.run import run_item_names
from wowahf.jobs.manager import Job, JobManager
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import STATS_JOBS
from wowahf.utils.http_date import naive_utc_second

# Blizzard AH dumps refresh hourly; wait a bit past LM so the new
# snapshot is published before we fetch.
PARSE_INTERVAL = timedelta(hours=1, minutes=10)
ITEMS_INTERVAL = timedelta(minutes=10)
EVENT_HEALTH_INTERVAL = timedelta(minutes=10)


def _parse_due_at() -> datetime:
    """
    Next parse = last parses.parsed_at (Blizzard Last-Modified) + 1h10m.

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
    first_stats = STATS_JOBS[0][0]
    mgr.register(
        Job(
            name="parse",
            fn=run_parse,
            due_at=_parse_due_at,
            chain=(first_stats,),
        )
    )
    for i, (name, fn) in enumerate(STATS_JOBS):
        nxt = (STATS_JOBS[i + 1][0],) if i + 1 < len(STATS_JOBS) else ()
        mgr.register(
            Job(
                name=name,
                fn=fn,
                chain=nxt,
                # Only the first stats job boots the chain on startup.
                run_on_startup=(i == 0),
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
