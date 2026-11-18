from datetime import timedelta

from wowahf.events.run import run_event_health
from wowahf.jobs.manager import Job, JobManager
from wowahf.pipeline.run import run_parse
from wowahf.stats.run import STATS_JOBS

# Poll shared parses/ for new fetcher gzips.
PARSE_POLL_INTERVAL = timedelta(minutes=1)
EVENT_HEALTH_INTERVAL = timedelta(minutes=10)


def build_job_manager() -> JobManager:
    mgr = JobManager()
    first_stats = STATS_JOBS[0][0]
    mgr.register(
        Job(
            name="parse",
            fn=run_parse,
            interval=PARSE_POLL_INTERVAL,
            chain=(first_stats,),
            run_on_startup=True,
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
            name="event_health",
            fn=run_event_health,
            interval=EVENT_HEALTH_INTERVAL,
            run_on_startup=True,
        )
    )
    return mgr
