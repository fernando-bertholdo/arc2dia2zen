"""Small file helpers: hashing, atomic writes, timestamps, default paths."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Union

PathLike = Union[str, "os.PathLike[str]"]

ARC_SIDEBAR = Path("~/Library/Application Support/Arc/StorableSidebar.json")
DIA_SUPPORT_DIR = Path("~/Library/Application Support/Dia")
DIA_DEFAULTS_DOMAIN = "company.thebrowser.dia"
DEFAULT_BACKUP_ROOT = Path("~/arc2dia2zen-backups")
# Backups went here before the rename to arc2dia2zen (0.2.0); restore and status still look.
LEGACY_BACKUP_ROOT = Path("~/arc2dia-backups")
DEFAULT_OUT_DIR = Path("arc2dia2zen-out")
DEFAULT_PLAN_FILE = Path("arc2dia2zen-plan.json")


def expand(path: PathLike) -> Path:
    return Path(path).expanduser()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def write_bytes_atomic(path: PathLike, data: bytes) -> None:
    """Write via a temporary file in the same directory, then rename it into place."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.arc2dia2zen-tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def copy_file_atomic(src: PathLike, dst: PathLike) -> None:
    """Copy ``src`` to ``dst`` (contents, mode and times) through a temporary file."""
    dst = Path(dst)
    tmp = dst.with_name(f".{dst.name}.arc2dia2zen-tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def write_json(path: PathLike, data: Any) -> None:
    write_bytes_atomic(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def read_json(path: PathLike) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def unique_dir(parent: Path, name: str) -> Path:
    """``parent/name``, or ``parent/name-2``, ``-3``... if it already exists."""
    candidate = parent / name
    n = 2
    while candidate.exists():
        candidate = parent / f"{name}-{n}"
        n += 1
    return candidate


def unique_file(path: Path) -> Path:
    candidate = path
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate
