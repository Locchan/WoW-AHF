from datetime import timedelta

from wowahf.events.run import run_event_health
from wowahf.items.run import run_item_names
from wowahf.jobs.manager import Job, JobManager
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import run_stats

PARSE_INTERVAL = timedelta(minutes=10)
ITEMS_INTERVAL = timedelta(minutes=10)
EVENT_HEALTH_INTERVAL = timedelta(minutes=10)


def build_job_manager() -> JobManager:
    mgr = JobManager()
    mgr.register(
        Job(
            name="parse",
            fn=run_parse,
            interval=PARSE_INTERVAL,
            chain=("stats",),
            run_on_startup=True,
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



