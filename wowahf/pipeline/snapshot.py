"""Durable Blizzard AH snapshots next to config (/mounted/parses), always gzip."""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import ijson

from wowahf.utils import DATA_DIR
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log

SNAPSHOT_KEEP = 48
_PARSES_DIRNAME = "parses"

_PARSE_NAME = re.compile(r"^parse_(\d+)\.json\.gz$")
_PENDING_NAME = re.compile(r"^pending_(\d{8}T\d{6})\.json\.gz$")


def snapshot_dir() -> Path:
    d = DATA_DIR / _PARSES_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def lm_token(last_modified: datetime) -> str:
    """Compact UTC stamp used in pending filenames."""
    return naive_utc_second(last_modified).strftime("%Y%m%dT%H%M%S")


def snapshot_path(parse_id: int) -> Path:
    return snapshot_dir() / f"parse_{parse_id}.json.gz"


def snapshot_partial_path(parse_id: int) -> Path:
    return snapshot_dir() / f"parse_{parse_id}.json.gz.partial"


def pending_path(last_modified: datetime) -> Path:
    return snapshot_dir() / f"pending_{lm_token(last_modified)}.json.gz"


def pending_partial_path(last_modified: datetime) -> Path:
    return snapshot_dir() / f"pending_{lm_token(last_modified)}.json.gz.partial"


def snapshot_exists(parse_id: int) -> bool:
    return snapshot_path(parse_id).is_file()


@dataclass(frozen=True)
class SnapshotFile:
    path: Path
    last_modified: datetime
    parse_id: int | None  # set for parse_N.json.gz; None for pending_*


def _house_jsonable(house: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": house["id"],
        "name": house.get("name", house["id"]),
        "auctions": house.get("auctions") or [],
        "commodities": house.get("commodities") or [],
    }
    lm = house.get("last_modified")
    if lm is not None:
        out["last_modified"] = naive_utc_second(lm).isoformat()
    return out


def write_snapshot(
    last_modified: datetime,
    houses: Iterator[dict[str, Any]],
    *,
    parse_id: int | None = None,
) -> Path:
    """
    Stream-write gzip JSON
    {"date", "parse_id", "data": {"houses": [...]}}
    one house at a time so peak RSS stays near a single AH dump.

    With parse_id: write parse_{id}.json.gz.
    Without: write pending_{lm}.json.gz (DB may be down).
    """
    if parse_id is not None:
        path = snapshot_path(parse_id)
        partial = snapshot_partial_path(parse_id)
    else:
        path = pending_path(last_modified)
        partial = pending_partial_path(last_modified)
    if partial.exists():
        partial.unlink()
    date_s = naive_utc_second(last_modified).isoformat()
    with gzip.open(partial, "wt", encoding="utf-8", compresslevel=6) as f:
        f.write('{"date":')
        json.dump(date_s, f)
        f.write(',"parse_id":')
        json.dump(parse_id, f)
        f.write(',"data":{"houses":[')
        first = True
        count = 0
        for house in houses:
            if not first:
                f.write(",")
            json.dump(_house_jsonable(house), f, separators=(",", ":"))
            first = False
            count += 1
        f.write("]}}")
    partial.replace(path)
    plain = snapshot_dir() / (
        f"parse_{parse_id}.json"
        if parse_id is not None
        else f"pending_{lm_token(last_modified)}.json"
    )
    plain.unlink(missing_ok=True)
    log(f"snapshot: wrote {path.name} houses={count}")
    return path


def open_snapshot_path(path: Path):
    """Open gzip snapshot as binary IO for ijson. Caller must close."""
    if not path.is_file():
        raise FileNotFoundError(f"no snapshot at {path}")
    return gzip.open(path, "rb")


def open_snapshot(parse_id: int):
    return open_snapshot_path(snapshot_path(parse_id))


def load_snapshot_meta_at(path: Path) -> tuple[datetime, int | None]:
    """Return (last_modified, parse_id or None) without loading house bodies."""
    with open_snapshot_path(path) as f:
        date_s = None
        pid: int | None = None
        saw_parse_id = False
        for prefix, event, value in ijson.parse(f):
            if prefix == "date" and event == "string":
                date_s = value
            elif prefix == "parse_id":
                saw_parse_id = True
                if event in ("number",) and value is not None:
                    pid = int(value)
                elif event == "null":
                    pid = None
                elif event == "string" and value not in ("", "null"):
                    pid = int(value)
            if date_s is not None and saw_parse_id:
                break
    if date_s is None:
        raise ValueError(f"snapshot {path.name} missing date")
    return naive_utc_second(datetime.fromisoformat(date_s)), pid


def load_snapshot_meta(parse_id: int) -> tuple[str, int]:
    """Return (date ISO, parse_id) for parse_{id}.json.gz."""
    lm, pid = load_snapshot_meta_at(snapshot_path(parse_id))
    if pid is None:
        pid = parse_id
    return lm.isoformat(), int(pid)


def iter_snapshot_houses_at(path: Path) -> Iterator[dict[str, Any]]:
    """Yield house dicts from a gzip snapshot without loading the whole file."""
    with open_snapshot_path(path) as f:
        for house in ijson.items(f, "data.houses.item"):
            lm = house.get("last_modified")
            if isinstance(lm, str):
                try:
                    house["last_modified"] = datetime.fromisoformat(lm)
                except ValueError:
                    pass
            yield house


def snapshot_house_ids(path: Path) -> set[int]:
    """House ids present in a gzip snapshot (streams; discards listing payloads)."""
    ids: set[int] = set()
    with open_snapshot_path(path) as f:
        for house in ijson.items(f, "data.houses.item"):
            ids.add(int(house["id"]))
    return ids


def iter_snapshot_houses(parse_id: int) -> Iterator[dict[str, Any]]:
    return iter_snapshot_houses_at(snapshot_path(parse_id))


def bind_pending_to_parse(path: Path, parse_id: int) -> Path:
    """Rename pending_*.json.gz → parse_{id}.json.gz after a DB row exists."""
    dest = snapshot_path(parse_id)
    if path.resolve() == dest.resolve():
        return dest
    if dest.exists():
        dest.unlink()
    path.replace(dest)
    return dest


def delete_snapshot(parse_id: int) -> None:
    snapshot_path(parse_id).unlink(missing_ok=True)
    snapshot_partial_path(parse_id).unlink(missing_ok=True)
    (snapshot_dir() / f"parse_{parse_id}.json").unlink(missing_ok=True)


def delete_snapshot_path(path: Path) -> None:
    path.unlink(missing_ok=True)
    partial = Path(str(path) + ".partial")
    partial.unlink(missing_ok=True)


def _snapshot_from_path(path: Path) -> SnapshotFile | None:
    name = path.name
    m_parse = _PARSE_NAME.match(name)
    if m_parse:
        parse_id = int(m_parse.group(1))
        try:
            lm, _meta_pid = load_snapshot_meta_at(path)
        except Exception as exc:
            log(f"snapshot: skip corrupt {name}: {exc}")
            return None
        return SnapshotFile(path=path, last_modified=lm, parse_id=parse_id)
    m_pending = _PENDING_NAME.match(name)
    if m_pending:
        try:
            lm, _meta_pid = load_snapshot_meta_at(path)
        except Exception:
            try:
                lm = datetime.strptime(m_pending.group(1), "%Y%m%dT%H%M%S")
            except ValueError:
                log(f"snapshot: skip bad pending name {name}")
                return None
        return SnapshotFile(path=path, last_modified=naive_utc_second(lm), parse_id=None)
    return None


def list_snapshot_files() -> list[SnapshotFile]:
    """Complete gzip snapshots on disk (parse_* and pending_*), oldest first."""
    out: list[SnapshotFile] = []
    for path in sorted(snapshot_dir().iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith(".partial"):
            continue
        snap = _snapshot_from_path(path)
        if snap is not None:
            out.append(snap)
    out.sort(key=lambda s: (s.last_modified, s.parse_id or -1))
    return out


def find_snapshot_for_lm(last_modified: datetime) -> SnapshotFile | None:
    """Any complete snapshot whose embedded/filename LM matches."""
    want = naive_utc_second(last_modified)
    pending = pending_path(want)
    if pending.is_file():
        snap = _snapshot_from_path(pending)
        if snap is not None:
            return snap
    for snap in list_snapshot_files():
        if snap.last_modified == want:
            return snap
    return None


def newest_snapshot_lm() -> datetime | None:
    files = list_snapshot_files()
    if not files:
        return None
    return max(s.last_modified for s in files)


def cleanup_old_snapshots(keep: int = SNAPSHOT_KEEP) -> int:
    """Keep the newest `keep` complete snapshots; delete older ones."""
    files = list_snapshot_files()
    if len(files) <= keep:
        return 0
    # Prefer keeping by LM, one file per LM (parse_* over pending_*).
    by_lm: dict[datetime, list[SnapshotFile]] = {}
    for snap in files:
        by_lm.setdefault(snap.last_modified, []).append(snap)
    ordered_lms = sorted(by_lm.keys())
    drop_lms = ordered_lms[: len(ordered_lms) - keep]
    removed = 0
    for lm in drop_lms:
        for snap in by_lm[lm]:
            snap.path.unlink(missing_ok=True)
            log(f"snapshot: removed old {snap.path.name}")
            removed += 1
    # Drop stray partials.
    for path in snapshot_dir().iterdir():
        if path.name.endswith(".partial"):
            path.unlink(missing_ok=True)
    return removed
