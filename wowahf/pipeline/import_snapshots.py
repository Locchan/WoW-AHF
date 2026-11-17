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
    snapshot_house_ids,
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
    Disk snapshots that never finished ingest: not ok, or missing raw houses
    that are present in the gzip (typical after deadlock mid-import).
    """
    ok_ids = list_ok_parse_ids(conn)
    missed: list[SnapshotFile] = []
    for snap in list_snapshot_files():
        if snap.parse_id is None or not snap.path.exists():
            continue
        parse_id = int(snap.parse_id)
        if parse_id not in ok_ids:
            missed.append(snap)
            continue
        snap_houses = snapshot_house_ids(snap.path)
        if not snap_houses:
            continue
        raw_houses = _raw_house_ids_for_parse(conn, parse_id)
        missing = snap_houses - raw_houses
        if missing:
            log(
                f"reingest: parse_id={parse_id} incomplete raw "
                f"missing_houses={sorted(missing)}"
            )
            missed.append(snap)
    missed.sort(key=lambda s: int(s.parse_id or 0))
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
