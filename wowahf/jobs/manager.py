from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested, wait

JobFn = Callable[[], None]
DueFn = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class Job:
    name: str
    fn: JobFn
    interval: timedelta | None = None
    """Fixed period after each successful finish. Ignored when due_at is set."""
    due_at: DueFn | None = None
    """Absolute next-run time (e.g. last_parse + 1h). Rechecked each wake."""
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

    def register(self, job: Job) -> None:
        if job.name in self._jobs:
            raise ValueError(f"job already registered: {job.name}")
        self._jobs[job.name] = job

    def spawn(self, name: str) -> None:
        """Start a job in a new thread (skip if unknown, stopping, or already running)."""
        if stop_requested():
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

    def _thread_main(self, name: str) -> None:
        job = self._jobs[name]
        ok = False
        try:
            if stop_requested():
                return
            log(f"jobs: starting {name}")
            try:
                result = job.fn()
            except Exception as exc:
                log(f"jobs: {name} failed: {exc}")
                return
            if stop_requested():
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
            with self._lock:
                self._running.discard(name)
                if (
                    ok
                    and job.interval is not None
                    and job.due_at is None
                ):
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
            now = _utc_now()
            soonest: datetime | None = None

            for name, job in self._jobs.items():
                if stop_requested():
                    break

                with self._lock:
                    busy = name in self._running

                if job.due_at is not None:
                    try:
                        due = job.due_at()
                    except Exception as exc:
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
        """Block until stop is requested, then join active job threads."""
        while not stop_requested():
            wait(0.5)
        log("jobs: stop requested, waiting for running jobs")
        deadline = _utc_now() + timedelta(minutes=2)
        while True:
            with self._lock:
                alive = [t for t in self._threads if t.is_alive()]
                self._threads = alive
            if not alive:
                break
            if _utc_now() >= deadline:
                log(f"jobs: giving up on {len(alive)} still-running thread(s)")
                break
            alive[0].join(timeout=0.5)
        log("jobs: manager stopped")
