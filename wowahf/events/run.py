from datetime import UTC, datetime, timedelta

from wowahf.db.connection import connect
from wowahf.utils.log import log

# pg_cron job names installed by 010_pg_baseline.
TRACKED_EVENTS: dict[str, timedelta] = {
    "purge_ah_raw_daily": timedelta(days=1),
    "cull_ah_watch_8h": timedelta(hours=8),
}

OVERDUE_GRACE = timedelta(minutes=30)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def run_event_health() -> None:
    """
    Check pg_cron jobs we own and recent schema_event_runs outcomes.
    """
    log("event_health: started")
    conn = connect()
    errors = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT extname FROM pg_extension WHERE extname = 'pg_cron'
                """
            )
            if cur.fetchone() is None:
                log("ERROR: event_health: extension pg_cron is not installed")
                errors += 1
            else:
                # cron.job is role-scoped; LocInfra exposes our jobs via
                # public.ahf_cron_jobs() (SECURITY DEFINER).
                cur.execute(
                    """
                    SELECT 1 FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public' AND p.proname = 'ahf_cron_jobs'
                    """
                )
                if cur.fetchone() is None:
                    log(
                        "ERROR: event_health: public.ahf_cron_jobs() missing "
                        "(run LocInfra setup-auth)"
                    )
                    errors += 1
                else:
                    cur.execute(
                        "SELECT jobname, schedule, active FROM public.ahf_cron_jobs()"
                    )
                    rows = {r[0]: r for r in cur.fetchall()}
                    for name, interval in TRACKED_EVENTS.items():
                        if name not in rows:
                            log(
                                f"ERROR: event_health: cron job {name!r} missing"
                            )
                            errors += 1
                            continue
                        _jobname, _schedule, active = rows[name]
                        if not active:
                            log(
                                f"ERROR: event_health: cron job {name!r} inactive"
                            )
                            errors += 1


            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'schema_event_runs'
                """
            )
            if cur.fetchone():
                for name, interval in TRACKED_EVENTS.items():
                    cur.execute(
                        """
                        SELECT ok, error_message, started_at, finished_at
                        FROM schema_event_runs
                        WHERE event_name = %s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (name,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        continue
                    ok, err, started, finished = row
                    if ok is False:
                        log(
                            f"ERROR: event_health: event {name!r} last run FAILED "
                            f"started={started} finished={finished} "
                            f"error={err!r}"
                        )
                        errors += 1
                    elif ok is None and started is not None:
                        started_n = _naive(started)
                        if started_n and _utc_now() > started_n + timedelta(hours=2):
                            log(
                                f"ERROR: event_health: event {name!r} last run "
                                f"stuck since {started}"
                            )
                            errors += 1
                    elif ok is True:
                        last = _naive(finished) or _naive(started)
                        if last and _utc_now() > last + interval + OVERDUE_GRACE:
                            log(
                                f"ERROR: event_health: event {name!r} overdue "
                                f"last={last.isoformat()} interval={interval}"
                            )
                            errors += 1

        if errors:
            log(f"event_health: finished with {errors} error(s)")
        else:
            log("event_health: finished ok")
    finally:
        conn.close()
