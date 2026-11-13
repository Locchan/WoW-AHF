"""Durable Blizzard AH snapshots next to config (/mounted/parses), always gzip."""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import ijson

from wowahf.utils import DATA_DIR
from wowahf.utils.http_date import naive_utc_second
from wowahf.utils.log import log

SNAPSHOT_KEEP = 48
_PARSES_DIRNAME = "parses"


def snapshot_dir() -> Path:
    d = DATA_DIR / _PARSES_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_path(parse_id: int) -> Path:
    return snapshot_dir() / f"parse_{parse_id}.json.gz"


def snapshot_partial_path(parse_id: int) -> Path:
    return snapshot_dir() / f"parse_{parse_id}.json.gz.partial"


def snapshot_exists(parse_id: int) -> bool:
    return snapshot_path(parse_id).is_file()


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
    parse_id: int,
    last_modified: datetime,
    houses: Iterator[dict[str, Any]],
) -> Path:
    """
    Stream-write gzip JSON
    {"date", "parse_id", "data": {"houses": [...]}}
    one house at a time so peak RSS stays near a single AH dump.
    """
    path = snapshot_path(parse_id)
    partial = snapshot_partial_path(parse_id)
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
    # Drop any leftover plain JSON from older builds.
    plain = snapshot_dir() / f"parse_{parse_id}.json"
    plain.unlink(missing_ok=True)
    log(f"snapshot: wrote {path.name} houses={count}")
    return path


def open_snapshot(parse_id: int):
    """Open gzip snapshot as binary IO for ijson. Caller must close."""
    path = snapshot_path(parse_id)
    if not path.is_file():
        raise FileNotFoundError(f"no snapshot for parse_id={parse_id}")
    return gzip.open(path, "rb")


def load_snapshot_meta(parse_id: int) -> tuple[str, int]:
    """Return (date ISO, parse_id) without loading house bodies."""
    with open_snapshot(parse_id) as f:
        date_s = None
        pid = None
        for prefix, event, value in ijson.parse(f):
            if prefix == "date" and event == "string":
                date_s = value
            elif prefix == "parse_id" and event in ("number", "string"):
                pid = int(value)
            if date_s is not None and pid is not None:
                break
    if date_s is None or pid is None:
        raise ValueError(f"snapshot parse_id={parse_id} missing date/parse_id")
    return str(date_s), int(pid)


def iter_snapshot_houses(parse_id: int) -> Iterator[dict[str, Any]]:
    """Yield house dicts from a gzip snapshot without loading the whole file."""
    with open_snapshot(parse_id) as f:
        for house in ijson.items(f, "data.houses.item"):
            lm = house.get("last_modified")
            if isinstance(lm, str):
                try:
                    house["last_modified"] = datetime.fromisoformat(lm)
                except ValueError:
                    pass
            yield house


def delete_snapshot(parse_id: int) -> None:
    snapshot_path(parse_id).unlink(missing_ok=True)
    snapshot_partial_path(parse_id).unlink(missing_ok=True)
    (snapshot_dir() / f"parse_{parse_id}.json").unlink(missing_ok=True)


def cleanup_old_snapshots(keep: int = SNAPSHOT_KEEP) -> int:
    """Keep the newest `keep` gzip snapshots; delete older parse_ids."""
    by_id: dict[int, list[Path]] = {}
    for path in snapshot_dir().iterdir():
        name = path.name
        if not name.startswith("parse_"):
            continue
        stem = name
        for suffix in (".json.gz.partial", ".json.gz", ".json"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        try:
            parse_id = int(stem.removeprefix("parse_"))
        except ValueError:
            continue
        by_id.setdefault(parse_id, []).append(path)

    if len(by_id) <= keep:
        return 0
    ordered = sorted(by_id.keys())
    drop = ordered[: len(ordered) - keep]
    for parse_id in drop:
        for path in by_id[parse_id]:
            path.unlink(missing_ok=True)
        log(f"snapshot: removed old parse_id={parse_id}")
    return len(drop)
