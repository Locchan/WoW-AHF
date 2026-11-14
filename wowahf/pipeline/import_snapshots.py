"""Import on-disk gzip snapshots that are missing from (or incomplete in) DB."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from psycopg import Connection

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
            ingest_snapshot(
                conn,
                parse_id,
                now=now,
                houses=iter_snapshot_houses_at(path),
            )
            invalidate_metrics_from(conn, parse_id)
            ok_ids.add(parse_id)
            imported.append(parse_id)
        except Exception as exc:
            log(f"import: failed parse_id={parse_id} file={path.name}: {exc}")
            raise
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
