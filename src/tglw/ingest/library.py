"""The user's document library: names, PDF copies, registry rows and PDF metadata rows.

Layout (tglw.common.paths, product mode): DOC_DIR holds <name>.pdf copies; CACHE_DIR one Docling
bundle per PDF sha256; DATA/common/docs.json the registry and DATA/common/pdf_meta.json outlines
and page labels (the rows scripts/build_pdf_meta.py writes for the research PDFs).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pypdf

from tglw import HOME_ENV
from tglw.common import paths, registry

DEFAULT_CLS = "doc"
_UNSAFE = re.compile(r"[^A-Za-z0-9._()\-]+")
MAX_NAME = 80


class LibraryError(ValueError):
    """A request the library cannot carry out as given (bad name, research data root, ...)."""


def check_writable() -> None:
    """Refuse to write into a research checkout's delivered data: those belong to their tasks."""
    if paths.RESEARCH:
        raise LibraryError("this is a research checkout (its data/ belongs to the sidecar tasks); "
                           f"set {HOME_ENV} to a library directory to ingest PDFs")


@contextmanager
def locked() -> Iterator[None]:
    """One ingest at a time per library: the registry, task MANIFESTs and the index are shared files."""
    path = paths.COMMON_DATA / ".ingest.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            import fcntl
        except ImportError:  # Windows: no advisory lock
            yield
            return
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def slug(stem: str) -> str:
    """Registered name from a file name: keep letters, digits and . _ ( ) -, the rest becomes "-"."""
    name = _UNSAFE.sub("-", stem).strip("-.")[:MAX_NAME].rstrip("-.")
    if not name:
        raise LibraryError(f"cannot make a document name from {stem!r}; pass --name")
    return name


def valid_name(name: str) -> str:
    if slug(name) != name:
        raise LibraryError(f"document name {name!r} may only use letters, digits and . _ ( ) - (no ':')")
    return name


def by_sha(sha: str) -> registry.DocInfo | None:
    return next((d for d in registry.docs(cached_only=False) if d.sha256 == sha), None)


def free_name(base: str) -> str:
    taken = {d.name for d in registry.docs(cached_only=False)}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def copy_pdf(src: Path, name: str) -> str:
    """Copy the PDF into DOC_DIR as <name>.pdf (atomic); return the registry's relative path."""
    rel = f"{name}.pdf"
    dest = paths.DOC_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    return rel


def page_count(pdf: Path) -> int:
    return len(pypdf.PdfReader(pdf).pages)


def _walk(reader: pypdf.PdfReader, items: list, level: int, out: list[dict[str, Any]]) -> None:
    for it in items:
        if isinstance(it, list):
            _walk(reader, it, level + 1, out)
            continue
        try:
            page = reader.get_destination_page_number(it) + 1
        except Exception:  # noqa: BLE001 - a broken destination is just a bookmark without a page
            page = None
        out.append({"level": level, "title": str(it.title), "page": page})


def pdf_meta_row(name: str, pdf: Path) -> dict[str, Any]:
    """Outline and /PageLabels of one PDF, as scripts/build_pdf_meta.py writes them."""
    reader = pypdf.PdfReader(pdf)
    outline: list[dict[str, Any]] = []
    try:
        _walk(reader, reader.outline, 1, outline)
    except Exception:  # noqa: BLE001 - unreadable outline trees are treated as absent
        outline = []
    has_labels = "/PageLabels" in reader.trailer["/Root"]
    return {"name": name, "pages": len(reader.pages), "outline": outline,
            "outline_depth": max((o["level"] for o in outline), default=0),
            "page_labels": list(reader.page_labels) if has_labels else None}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def put_pdf_meta(row: dict[str, Any]) -> None:
    path = paths.COMMON_DATA / "pdf_meta.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    rows = [r for r in rows if r["name"] != row["name"]] + [row]
    _write_json(path, rows)


def drop_pdf_meta(name: str) -> None:
    path = paths.COMMON_DATA / "pdf_meta.json"
    if path.exists():
        _write_json(path, [r for r in json.loads(path.read_text()) if r["name"] != name])


def info(name: str, cls: str, rel_pdf: str, sha: str, pages: int, cached: bool, source: str) -> registry.DocInfo:
    return registry.DocInfo(name=name, cls=cls, pdf=rel_pdf, sha256=sha, pages=pages, cached=cached, cases=(),
                            source=source, added=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
