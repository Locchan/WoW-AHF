"""Import on-disk gzip snapshots that are missing from (or incomplete in) DB."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from psycopg import Connection

from wowahf.db.connection import (
    acquire_ingest_lock,
    release_ingest_lock,
    retry_on_transient_db,
)
from wowahf.db.house_tables import SCHEMA_RAW
from wowahf.db.writes import (
    create_parse,
    ensure_parse_id,
    find_incomplete_parse_for_lm,
    find_ok_parse_for_lm,
    list_ok_parse_ids,
)
from wowahf.pipeline.ingest import ingest_snapshot, invalidate_metrics_from
from wowahf.pipeline.snapshot import (
    SnapshotFile,
    bind_pending_to_parse,
    iter_snapshot_houses_at,
    list_snapshot_files,
)
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log
from wowahf.utils.shutdown import stop_requested


def import_missing_snapshots(
    conn: Connection, *, now: datetime | None = None
) -> list[int]:
    """
    For every complete snapshot file on disk, ensure an ok parse exists.

    - parse_{id}.json.gz → ensure row id, ingest if not ok
    - pending_{lm}.json.gz → create/reuse parse for LM, rename, ingest
    Returns parse ids that were (re)ingested.
    """
    now = now or datetime.now(UTC)
    ok_ids = list_ok_parse_ids(conn)
    imported: list[int] = []

    acquire_ingest_lock(conn)
    try:
        for snap in list_snapshot_files():
            if stop_requested():
                break
            resolved = _resolve_for_import(conn, snap, ok_ids)
            if resolved is None:
                continue
            parse_id, path = resolved
            if parse_id in ok_ids:
                continue
            log(
                f"import: ingesting {path.name} → parse_id={parse_id} "
                f"lm={snap.last_modified.isoformat()}"
            )
            try:
                retry_on_transient_db(
                    lambda pid=parse_id, p=path: ingest_snapshot(
                        conn,
                        pid,
                        now=now,
                        houses=iter_snapshot_houses_at(p),
                    ),
                    label=f"ingest parse_id={parse_id}",
                )
                invalidate_metrics_from(conn, parse_id)
                ok_ids.add(parse_id)
                imported.append(parse_id)
            except Exception as exc:
                log(
                    f"import: failed parse_id={parse_id} "
                    f"file={path.name}: {exc}"
                )
                raise
    finally:
        release_ingest_lock(conn)
    return imported


def _raw_house_ids_for_parse(conn: Connection, parse_id: int) -> set[int]:
    """AH ids that have at least one ah_raw row for this parse."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = %s
              AND tablename ~ '^ah_raw_[0-9]+$'
            """,
            (SCHEMA_RAW,),
        )
        tables = [r[0] for r in cur.fetchall()]
    out: set[int] = set()
    with conn.cursor() as cur:
        for table in tables:
            house_id = int(table.rsplit("_", 1)[-1])
            cur.execute(
                f"SELECT 1 FROM {SCHEMA_RAW}.{table} "
                f"WHERE parse_id = %s LIMIT 1",
                (parse_id,),
            )
            if cur.fetchone():
                out.add(house_id)
    return out


def list_missed_snapshot_parses(conn: Connection) -> list[SnapshotFile]:
    """
    Disk snapshots that never finished ingest: not ok, or fewer raw houses
    than a complete sibling parse (typical after deadlock mid-import).

    Uses DB house counts only (no gzip scan) against the max raw-house count
    among ok parses that still have a snapshot on disk.
    """
    by_id = {
        int(s.parse_id): s
        for s in list_snapshot_files()
        if s.parse_id is not None and s.path.exists()
    }
    if not by_id:
        return []

    ok_ids = list_ok_parse_ids(conn)
    raw_counts: dict[int, int] = {}
    for parse_id in by_id:
        raw_counts[parse_id] = len(_raw_house_ids_for_parse(conn, parse_id))

    complete_n = max(
        (raw_counts[pid] for pid in by_id if pid in ok_ids),
        default=0,
    )
    missed: list[SnapshotFile] = []
    for parse_id, snap in sorted(by_id.items()):
        if parse_id not in ok_ids:
            log(f"reingest: parse_id={parse_id} not ok")
            missed.append(snap)
            continue
        n = raw_counts.get(parse_id, 0)
        # n==0 means raw already purged by retention — not an incomplete ingest.
        if n == 0 or complete_n <= 0 or n >= complete_n:
            continue
        log(
            f"reingest: parse_id={parse_id} incomplete raw "
            f"houses={n} expected>={complete_n}"
        )
        missed.append(snap)
    return missed


def reingest_missed_snapshots(
    conn: Connection, *, now: datetime | None = None
) -> list[int]:
    """
    Force re-ingest only incomplete / not-ok snapshots on disk.

    Invalidates delta metrics once from the oldest reingested parse.
    """
    now = now or datetime.now(UTC)
    snaps = list_missed_snapshot_parses(conn)
    if not snaps:
        log("reingest: no missed snapshots")
        return []

    imported: list[int] = []
    acquire_ingest_lock(conn)
    try:
        for snap in snaps:
            if stop_requested():
                break
            parse_id = int(snap.parse_id)
            ensure_parse_id(
                conn, parse_id, naive_utc_second(snap.last_modified)
            )
            log(
                f"reingest: {snap.path.name} → parse_id={parse_id} "
                f"lm={snap.last_modified.isoformat()}"
            )
            try:
                conn.rollback()
            except Exception:
                pass
            retry_on_transient_db(
                lambda pid=parse_id, path=snap.path: ingest_snapshot(
                    conn,
                    pid,
                    now=now,
                    houses=iter_snapshot_houses_at(path),
                ),
                label=f"reingest parse_id={parse_id}",
            )
            imported.append(parse_id)
    finally:
        release_ingest_lock(conn)

    if imported:
        invalidate_metrics_from(conn, min(imported))
        log(
            f"reingest: finished missed n={len(imported)} "
            f"parse_ids={imported}"
        )
    return imported


def force_reingest_parse_range(
    conn: Connection,
    start_id: int,
    end_id: int,
    *,
    now: datetime | None = None,
    invalidate: bool = False,
) -> list[int]:
    """
    Force re-ingest ok parses in [start_id, end_id] from on-disk gzips.

    Use when raw was pruned but snapshots remain and ah_trans is empty
    (e.g. mid-outage chain). Sequential order restores prev raw for sale
    inference. Does not wipe later parses' trans unless invalidate=True.
    """
    from wowahf.db.writes import delete_delta_metrics, list_house_ids
    from wowahf.pipeline.snapshot import snapshot_path

    now = now or datetime.now(UTC)
    imported: list[int] = []
    acquire_ingest_lock(conn)
    try:
        for parse_id in range(int(start_id), int(end_id) + 1):
            if stop_requested():
                break
            path = snapshot_path(parse_id)
            if not path.exists():
                log(f"force_reingest: skip parse_id={parse_id} (no gzip)")
                continue
            log(f"force_reingest: parse_{parse_id}.json.gz → parse_id={parse_id}")
            try:
                conn.rollback()
            except Exception:
                pass
            retry_on_transient_db(
                lambda pid=parse_id, p=path: ingest_snapshot(
                    conn,
                    pid,
                    now=now,
                    houses=iter_snapshot_houses_at(p),
                ),
                label=f"force_reingest parse_id={parse_id}",
            )
            imported.append(parse_id)
    finally:
        release_ingest_lock(conn)

    if not imported:
        log("force_reingest: nothing reingested")
        return []

    if invalidate:
        invalidate_metrics_from(conn, min(imported))
    else:
        # Refresh delta stats / ITS / CMA only for the reingested window.
        house_ids = list_house_ids(conn)
        for parse_id in imported:
            for house_id in house_ids:
                delete_delta_metrics(conn, parse_id, house_id)
        log(
            f"force_reingest: cleared delta metrics for "
            f"parse_ids={imported[0]}..{imported[-1]}"
        )

    log(
        f"force_reingest: finished n={len(imported)} "
        f"parse_ids={imported[0]}..{imported[-1]}"
    )
    return imported


def _resolve_for_import(
    conn: Connection, snap: SnapshotFile, ok_ids: set[int]
) -> tuple[int, Path] | None:
    """Return (parse_id, path to read) or None if already ok / nothing to do."""
    lm = naive_utc_second(snap.last_modified)
    path = snap.path

    existing_ok = find_ok_parse_for_lm(conn, lm)
    if existing_ok is not None:
        if snap.parse_id is None and path.exists():
            bind_pending_to_parse(path, existing_ok)
        ok_ids.add(existing_ok)
        return None

    if snap.parse_id is not None:
        ensure_parse_id(conn, snap.parse_id, lm)
        return snap.parse_id, path

    incomplete = find_incomplete_parse_for_lm(conn, lm)
    if incomplete is not None:
        path = bind_pending_to_parse(path, incomplete)
        return incomplete, path

    parse_id = create_parse(conn, lm)
    path = bind_pending_to_parse(path, parse_id)
    log(f"import: reserved parse_id={parse_id} for LM={lm.isoformat()}")
    return parse_id, path
