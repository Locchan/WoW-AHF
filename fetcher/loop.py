"""Fetch Blizzard AH dumps on an LM+1h10m schedule; publish atomic gzips."""

from __future__ import annotations

import random
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from wowahf.blizzard.ah_fetcher import iter_ah_houses, peek_ah_last_modified
from wowahf.pipeline.schedule import PARSE_INTERVAL, PARSE_RETRY
from wowahf.pipeline.snapshot import (
    cleanup_old_snapshots,
    find_snapshot_for_lm,
    newest_snapshot_lm,
    pending_path,
    snapshot_dir,
    write_snapshot_to_dir,
)
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested, wait

_DOWNLOAD_RETRY_CAP_S = 60.0


def _utc_now() -> datetime:
    return naive_utc_second(datetime.now(UTC))


def _sleep_until(due: datetime) -> bool:
    """Sleep until ``due`` or stop. Returns True if stop was requested."""
    while not stop_requested():
        remain = (due - _utc_now()).total_seconds()
        if remain <= 0:
            return False
        if wait(min(30.0, remain)):
            return True
    return True


def _download_backoff(attempt: int, exc: BaseException) -> None:
    delay = min(_DOWNLOAD_RETRY_CAP_S, 1.0 * (2 ** min(attempt, 8)))
    delay *= 0.5 + random.random()
    log(
        f"fetcher: download failed ({exc.__class__.__name__}: {exc}); "
        f"retry in {delay:.1f}s (attempt {attempt + 1})"
    )
    time.sleep(delay)


def _publish_atomic(tmp_path: Path, last_modified: datetime) -> Path:
    """
    Copy tmp gzip into the shared parses dir via ``.partial`` then rename.

    Parsers ignore ``*.partial``, so a mid-copy file is never ingested.
    """
    dest = pending_path(last_modified)
    partial = Path(str(dest) + ".partial")
    snapshot_dir()
    if partial.exists():
        partial.unlink()
    shutil.copyfile(tmp_path, partial)
    partial.replace(dest)
    log(f"fetcher: published {dest.name}")
    return dest


def _attempt_fetch(known: datetime | None) -> tuple[str, datetime | None]:
    """
    One fetch cycle into /tmp then publish if LM advanced.

    Returns (status, new_known_lm):
      - ("published", lm) — new dump on disk
      - ("unchanged", known) — Blizzard LM not past known
      - ("error", known) — transient failure (caller backs off)
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="wow_ahf_fetch_"))
    try:
        lm = peek_ah_last_modified()
        if lm is None:
            raise RuntimeError("Blizzard returned no auction houses")
        lm = naive_utc_second(lm)

        if known is not None and lm <= known:
            log(
                f"fetcher: LM unchanged ({lm.isoformat()}); "
                f"retry in {PARSE_RETRY}"
            )
            return "unchanged", known

        existing = find_snapshot_for_lm(lm)
        if existing is not None:
            log(
                f"fetcher: LM {lm.isoformat()} already on disk "
                f"({existing.path.name})"
            )
            return "published", lm

        log(f"fetcher: downloading lm={lm.isoformat()}")
        tmp_path = write_snapshot_to_dir(
            tmp_root, lm, iter_ah_houses(), name_prefix="pending"
        )
        _publish_atomic(tmp_path, lm)
        cleanup_old_snapshots()
        log(
            f"fetcher: durable LM={lm.isoformat()} "
            f"next_due={(lm + PARSE_INTERVAL).isoformat()}"
        )
        return "published", lm
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def run_fetcher() -> None:
    """
    Forever loop:

    - next attempt at last_disk_LM + 1h10m (or immediately if none)
    - download to /tmp; if LM unchanged vs last known, wait 10m and retry
    - if LM advanced, atomically publish into shared parses/ and schedule
      next at new_LM + 1h10m
    """
    log("fetcher: ready (SIGTERM/SIGINT will shut down cleanly)")
    known = newest_snapshot_lm()
    if known is not None:
        known = naive_utc_second(known)
        log(f"fetcher: last on-disk LM={known.isoformat()}")
    else:
        log("fetcher: no on-disk snapshots yet; fetching immediately")

    while not stop_requested():
        due = (known + PARSE_INTERVAL) if known is not None else _utc_now()
        log(f"fetcher: next attempt due={due.isoformat()}")
        if _sleep_until(due):
            break

        attempt = 0
        while not stop_requested():
            try:
                status, known = _attempt_fetch(known)
            except Exception as exc:
                _download_backoff(attempt, exc)
                attempt += 1
                continue

            if status == "published":
                break
            # unchanged — wait 10m and try again without waiting full 1h10m
            if wait(PARSE_RETRY.total_seconds()):
                log("fetcher: shutdown complete")
                return
            attempt = 0

    log("fetcher: shutdown complete")
