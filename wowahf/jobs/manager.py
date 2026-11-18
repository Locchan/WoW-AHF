from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

from wowahf.db.connection import connect, is_db_unavailable, ping
from wowahf.db.writes import begin_job_run, finish_job_run
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested, wait

JobFn = Callable[[], None]
DueFn = Callable[[], datetime]

DB_PING_EVERY = timedelta(seconds=5)
DB_RETRY_EVERY_S = 5.0
DB_WAIT_LOG_EVERY_S = 300.0


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class Job:
    name: str
    fn: JobFn
    interval: timedelta | None = None
    """Fixed period after each finish (ok or failed). Ignored when due_at is set."""
    due_at: DueFn | None = None
    """Absolute next-run time (e.g. last_parse + 1h10m). Rechecked each wake."""
    chain: tuple[str, ...] = ()
    """Jobs to spawn after a successful run."""
    run_on_startup: bool = False


@dataclass
class JobManager:
    """Threaded job runner with interval / due_at schedules and success chains."""

    _jobs: dict[str, Job] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _running: set[str] = field(default_factory=set)
    _threads: list[threading.Thread] = field(default_factory=list)
    _next_interval_due: dict[str, datetime] = field(default_factory=dict)
    _scheduler: threading.Thread | None = None
    _db_down: bool = False
    _last_db_ping_ok: datetime | None = None

    def register(self, job: Job) -> None:
        if job.name in self._jobs:
            raise ValueError(f"job already registered: {job.name}")
        self._jobs[job.name] = job

    def spawn(self, name: str) -> None:
        """Start a job in a new thread (skip if unknown, stopping, or already running)."""
        if stop_requested():
            return
        with self._lock:
            if self._db_down:
                return
        if name not in self._jobs:
            log(f"jobs: unknown job '{name}'")
            return
        with self._lock:
            if name in self._running:
                log(f"jobs: {name} already running, skip")
                return
            self._running.add(name)
            thread = threading.Thread(
                target=self._thread_main,
                args=(name,),
                name=f"job-{name}",
                daemon=False,
            )
            self._threads.append(thread)
            thread.start()

    def _note_db_down(self, exc: BaseException) -> None:
        """Pause schedules; LED + console once per outage."""
        with self._lock:
            first = not self._db_down
            self._db_down = True
            self._last_db_ping_ok = None
        if not first:
            return
        msg = f"PostgreSQL unavailable — pausing all schedules: {exc}"
        log(f"jobs: ERROR {msg}")
        try:
            from wowahf.led import alert

            alert(f"wow_ahf: {msg}")
        except Exception:
            pass

    def _probe_db(self) -> None:
        """Raise if DB is unreachable. Throttled successful pings."""
        now = _utc_now()
        with self._lock:
            last_ok = self._last_db_ping_ok
        if last_ok is not None and now - last_ok < DB_PING_EVERY:
            return
        ping()
        with self._lock:
            self._last_db_ping_ok = _utc_now()

    def _wait_for_db_and_resume(self) -> None:
        log("jobs: waiting for PostgreSQL...")
        last_log = _utc_now()
        while not stop_requested():
            try:
                ping()
                break
            except Exception:
                now = _utc_now()
                if (now - last_log).total_seconds() >= DB_WAIT_LOG_EVERY_S:
                    log("jobs: still waiting for PostgreSQL...")
                    last_log = now
                if wait(DB_RETRY_EVERY_S):
                    return
        if stop_requested():
            return

        log("jobs: PostgreSQL is back — restarting schedules")
        with self._lock:
            self._db_down = False
            self._last_db_ping_ok = _utc_now()
            now = _utc_now()
            for job in self._jobs.values():
                if job.interval is not None and job.due_at is None:
                    self._next_interval_due[job.name] = now

        try:
            from wowahf.recover import recover_after_restart

            recover_after_restart()
        except Exception as exc:
            log(f"jobs: recover after outage failed: {exc}")
            if is_db_unavailable(exc):
                self._note_db_down(exc)
                return

        for job in self._jobs.values():
            if job.run_on_startup:
                self.spawn(job.name)

    def _thread_main(self, name: str) -> None:
        job = self._jobs[name]
        ok = False
        run_id: int | None = None
        conn = None
        try:
            if stop_requested():
                return
            with self._lock:
                if self._db_down:
                    return
            try:
                conn = connect()
                run_id = begin_job_run(conn, name)
            except Exception as exc:
                if is_db_unavailable(exc):
                    self._note_db_down(exc)
                    return
                log(f"jobs: {name} failed to open DB: {exc}")
                if not stop_requested():
                    from wowahf.led import alert

                    alert(f"wow_ahf job '{name}' failed: {exc}")
                return
            log(f"jobs: starting {name}")
            try:
                result = job.fn()
            except Exception as exc:
                if is_db_unavailable(exc):
                    self._note_db_down(exc)
                    return
                log(f"jobs: {name} failed: {exc}")
                if not stop_requested():
                    from wowahf.led import alert

                    alert(f"wow_ahf job '{name}' failed: {exc}")
                return
            if stop_requested():
                return
            with self._lock:
                if self._db_down:
                    return
            # False means intentional skip (e.g. parse: unchanged snapshot).
            if result is False:
                log(f"jobs: finished {name} (skipped)")
                ok = True
                return
            log(f"jobs: finished {name}")
            ok = True
            for nxt in job.chain:
                self.spawn(nxt)
        finally:
            if run_id is not None and conn is not None:
                try:
                    finish_job_run(conn, run_id, ok=ok)
                except Exception as exc:
                    if is_db_unavailable(exc):
                        self._note_db_down(exc)
                    else:
                        log(f"jobs: {name} failed to record run status: {exc}")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            with self._lock:
                self._running.discard(name)
                # Always push the next due forward — otherwise a failed run
                # leaves a past due and the 1s scheduler wake respawns forever.
                if job.interval is not None and job.due_at is None:
                    self._next_interval_due[name] = _utc_now() + job.interval

    def start(self) -> None:
        """Spawn startup jobs and begin the due/interval scheduler."""
        now = _utc_now()
        for job in self._jobs.values():
            if job.interval is not None and job.due_at is None:
                # First periodic fire: after interval, unless also run_on_startup
                # (startup run will set the next due when it finishes).
                if not job.run_on_startup:
                    self._next_interval_due[job.name] = now + job.interval

        for job in self._jobs.values():
            if job.run_on_startup:
                self.spawn(job.name)

        self._scheduler = threading.Thread(
            target=self._schedule_loop,
            name="job-scheduler",
            daemon=True,
        )
        self._scheduler.start()
        log("jobs: manager started")

    def _schedule_loop(self) -> None:
        while not stop_requested():
            with self._lock:
                paused = self._db_down
            if paused:
                self._wait_for_db_and_resume()
                continue

            try:
                self._probe_db()
            except Exception as exc:
                if is_db_unavailable(exc):
                    self._note_db_down(exc)
                    continue
                log(f"jobs: unexpected DB probe error: {exc}")
                if wait(1.0):
                    break
                continue

            now = _utc_now()
            soonest: datetime | None = None

            for name, job in self._jobs.items():
                if stop_requested():
                    break
                with self._lock:
                    if self._db_down:
                        break
                    busy = name in self._running

                if job.due_at is not None:
                    try:
                        due = job.due_at()
                    except Exception as exc:
                        if is_db_unavailable(exc):
                            self._note_db_down(exc)
                            break
                        log(f"jobs: {name} due_at failed: {exc}")
                        continue
                    if not busy and now >= due:
                        self.spawn(name)
                    elif not busy and (soonest is None or due < soonest):
                        soonest = due
                    continue

                if job.interval is not None:
                    with self._lock:
                        due = self._next_interval_due.get(name)
                    if due is None:
                        # Waiting for an in-flight startup/first run to finish.
                        continue
                    if not busy and now >= due:
                        self.spawn(name)
                    elif soonest is None or due < soonest:
                        soonest = due

            now = _utc_now()
            if soonest is None:
                sleep_s = 1.0
            else:
                sleep_s = min(1.0, max(0.05, (soonest - now).total_seconds()))
            if wait(sleep_s):
                break

        log("jobs: scheduler stopped")

    def wait_forever(self) -> None:
        """
        Block until stop is requested, then join active job threads.

        The parse job is waited on without a deadline so an in-flight Blizzard
        download can finish and land a durable gzip before the process exits.
        Other jobs keep a 2-minute join budget.
        """
        while not stop_requested():
            wait(0.5)
        log("jobs: stop requested, waiting for running jobs")
        deadline = _utc_now() + timedelta(minutes=2)
        logged_parse_wait = False
        while True:
            with self._lock:
                alive = [t for t in self._threads if t.is_alive()]
                self._threads = alive
            if not alive:
                break
            parse_alive = [t for t in alive if t.name == "job-parse"]
            if parse_alive:
                if not logged_parse_wait:
                    log(
                        "jobs: parse still running — waiting for download "
                        "to finish (no timeout)"
                    )
                    logged_parse_wait = True
                parse_alive[0].join(timeout=0.5)
                continue
            if _utc_now() >= deadline:
                log(f"jobs: giving up on {len(alive)} still-running thread(s)")
                break
            alive[0].join(timeout=0.5)
        log("jobs: manager stopped")
