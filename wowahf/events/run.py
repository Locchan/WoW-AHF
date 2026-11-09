from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wowahf.db.connection import connect
from wowahf.utils.log import log

# Events we install via migrations. Interval used for overdue checks.
TRACKED_EVENTS: dict[str, timedelta] = {
    "purge_ah_raw_daily": timedelta(days=1),
    "cull_ah_watch_8h": timedelta(hours=8),
}

# Allow some slack past the schedule before treating as missed.
OVERDUE_GRACE = timedelta(minutes=30)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def run_event_health() -> None:
    """
    Check MariaDB scheduled events we own.

    Native I_S.EVENTS has STATUS + LAST_EXECUTED but not success/failure.
    Failures are read from schema_event_runs (written by the procedures).
    """
    log("event_health: started")
    conn = connect()
    errors = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT @@event_scheduler")
            scheduler = str(cur.fetchone()[0]).upper()
            if scheduler not in ("ON", "1"):
                log(f"ERROR: event_health: event_scheduler is {scheduler!r} (expected ON)")
                errors += 1

            cur.execute(
                """
                SELECT EVENT_NAME, STATUS, LAST_EXECUTED, STARTS,
                       INTERVAL_VALUE, INTERVAL_FIELD
                FROM information_schema.EVENTS
                WHERE EVENT_SCHEMA = DATABASE()
                  AND EVENT_NAME IN (%s, %s)
                """,
                tuple(TRACKED_EVENTS.keys()),
            )
            rows = {r[0]: r for r in cur.fetchall()}

            for name, interval in TRACKED_EVENTS.items():
                if name not in rows:
                    log(f"ERROR: event_health: event {name!r} missing from DATABASE()")
                    errors += 1
                    continue

                _ename, status, last_executed, starts, _iv, _if = rows[name]
                if str(status).upper() != "ENABLED":
                    log(
                        f"ERROR: event_health: event {name!r} STATUS={status!r} "
                        "(expected ENABLED)"
                    )
                    errors += 1

                last = _naive(last_executed)
                start_at = _naive(starts)
                now = _utc_now()
                if last is None:
                    # Not yet run — only error once past first scheduled start + grace.
                    if start_at is not None and now > start_at + OVERDUE_GRACE:
                        log(
                            f"ERROR: event_health: event {name!r} never executed "
                            f"(STARTS={start_at.isoformat()})"
                        )
                        errors += 1
                elif now > last + interval + OVERDUE_GRACE:
                    log(
                        f"ERROR: event_health: event {name!r} overdue "
                        f"LAST_EXECUTED={last.isoformat()} "
                        f"interval={interval}"
                    )
                    errors += 1

            # Latest run outcome from our log table (may not exist pre-m009).
            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name = 'schema_event_runs'
                """
            )
            if cur.fetchone()[0]:
                for name in TRACKED_EVENTS:
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
                    if ok == 0:
                        log(
                            f"ERROR: event_health: event {name!r} last run FAILED "
                            f"started={started} finished={finished} "
                            f"error={err!r}"
                        )
                        errors += 1
                    elif ok is None and started is not None:
                        # Started but never finished (crash mid-run).
                        started_n = _naive(started)
                        if started_n and _utc_now() > started_n + timedelta(hours=2):
                            log(
                                f"ERROR: event_health: event {name!r} last run "
                                f"stuck since {started}"
                            )
                            errors += 1

        if errors:
            log(f"event_health: finished with {errors} error(s)")
        else:
            log("event_health: finished ok")
    finally:
        conn.close()
