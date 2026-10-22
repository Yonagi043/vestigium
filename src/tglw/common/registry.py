"""Registry of the documents: names, classes, cache keys, and the 6x10 cases that use them.

Rows live in DATA/common/docs.json. A research checkout lists its 61 corpus PDFs; a product
library (see tglw.common.paths) lists what the ingest command added, and starts empty (no docs.json yet).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .paths import CACHE_DIR, COMMON_DATA, DOC_DIR

REGISTRY = "docs.json"
STAMP = "library.stamp"


@dataclass(frozen=True, slots=True)
class DocInfo:
    name: str
    cls: str
    pdf: str
    sha256: str
    pages: int
    cached: bool
    cases: tuple[str, ...]
    source: str | None = None  # file name the user ingested (tglw.ingest)
    added: str | None = None  # ISO time tglw.ingest registered it

    @property
    def pdf_path(self) -> Path:
        return DOC_DIR / self.pdf

    @property
    def bundle_dir(self) -> Path:
        return CACHE_DIR / self.sha256


def _path() -> Path:
    return COMMON_DATA / REGISTRY


def _info(row: dict[str, Any]) -> DocInfo:
    return DocInfo(**{**row, "cases": tuple(row.get("cases") or ())})


@lru_cache(maxsize=1)
def _all() -> dict[str, DocInfo]:
    path = _path()
    rows = json.loads(path.read_text()) if path.exists() else []
    return {r["name"]: _info(r) for r in rows}


def reload() -> None:
    """Forget the cached registry, e.g. after tglw.ingest added a document in another process."""
    _all.cache_clear()


def stamp() -> tuple[int, int] | None:
    """Changes once tglw.ingest has finished adding, re-ingesting (--force) or removing a document
    (it rewrites STAMP last, after sidecars, index and report); a long-running server can compare
    it and call reload(). Without STAMP (a research checkout), docs.json's own stat."""
    for path in (COMMON_DATA / STAMP, _path()):
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        return st.st_mtime_ns, st.st_size
    return None


def touch() -> None:
    """Mark the library as changed (see stamp)."""
    path = COMMON_DATA / STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(str(time.time_ns()))
    os.replace(tmp, path)


def docs(cached_only: bool = True) -> list[DocInfo]:
    return [d for d in _all().values() if d.cached or not cached_only]


def get(name: str) -> DocInfo:
    return _all()[name]


def by_case(case_id: str) -> DocInfo:
    for d in _all().values():
        if case_id in d.cases:
            return d
    raise KeyError(case_id)


def _row(d: DocInfo) -> dict[str, Any]:
    row = {**asdict(d), "cases": list(d.cases)}
    return {k: v for k, v in row.items() if v is not None or k in ("name", "cls", "pdf", "sha256")}


def _save(infos: list[DocInfo]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps([_row(d) for d in infos], indent=1, ensure_ascii=False))
    os.replace(tmp, path)
    reload()


def put(info: DocInfo) -> None:
    """Add a document, or replace the row of the same name (order kept)."""
    rows = dict(_all())
    rows[info.name] = info
    _save(list(rows.values()))


def remove(name: str) -> DocInfo:
    rows = dict(_all())
    info = rows.pop(name)
    _save(list(rows.values()))
    return info
