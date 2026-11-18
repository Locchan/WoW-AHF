from __future__ import annotations

import gzip
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from string.templatelib import Interpolation, Template
from typing import TextIO

from wowahf.utils import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "wow_ahf.log"

_lock = threading.Lock()
_file: TextIO | None = None
_setup_done = False


def _render(template: Template) -> str:
    """Join a 3.14 t-string Template into a plain str for printing."""
    parts: list[str] = []
    for part in template:
        if isinstance(part, Interpolation):
            parts.append(str(part.value))
        else:
            parts.append(part)
    return "".join(parts)


def _gzip_log_file(path: Path) -> Path | None:
    """Compress a plain log file next to itself; remove the original."""
    if not path.is_file() or path.suffix == ".gz":
        return None
    stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime(
        "%Y%m%dT%H%M%S"
    )
    dest = path.with_name(f"{path.stem}.{stamp}.log.gz")
    n = 0
    while dest.exists():
        n += 1
        dest = path.with_name(f"{path.stem}.{stamp}-{n}.log.gz")
    with path.open("rb") as src, gzip.open(dest, "wb", compresslevel=6) as out:
        shutil.copyfileobj(src, out)
    path.unlink(missing_ok=True)
    return dest


def rotate_previous_logs() -> list[Path]:
    """
    Gzip leftover plain logs from earlier container runs.

    Keeps archived ``*.log.gz`` files; starts a fresh ``wow_ahf.log`` after.
    """
    rotated: list[Path] = []
    if not LOG_DIR.is_dir():
        return rotated
    for path in sorted(LOG_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix == ".gz" or not path.name.endswith(".log"):
            continue
        try:
            dest = _gzip_log_file(path)
        except OSError:
            continue
        if dest is not None:
            rotated.append(dest)
    return rotated


def setup_logging() -> None:
    """
    Rotate prior run logs and open ``DATA_DIR/logs/wow_ahf.log`` for append.

    Safe to call more than once; only the first call rotates and opens.
    """
    global _file, _setup_done
    with _lock:
        if _setup_done:
            return
        _setup_done = True
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            rotated = rotate_previous_logs()
            _file = LOG_FILE.open("a", encoding="utf-8", buffering=1)
        except OSError as exc:
            _file = None
            print(
                f"[log] file logging disabled ({LOG_FILE}): {exc}",
                flush=True,
            )
            return
        for dest in rotated:
            line = (
                f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}] "
                f"log: archived previous run → {dest.name}"
            )
            print(line, flush=True)
            _file.write(line + "\n")


def log(message: str) -> None:
    if not _setup_done:
        setup_logging()
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = _render(t"[{ts}] {message}")
    with _lock:
        print(line, flush=True)
        if _file is not None:
            try:
                _file.write(line + "\n")
            except OSError:
                pass
