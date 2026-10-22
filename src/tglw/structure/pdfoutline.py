"""PDF bookmarks with destination coordinates, cached under data/A/outline_xyz.json."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from tglw.common import pdfmeta, registry
from tglw.common.paths import DATA

CACHE = DATA / "A" / "outline_xyz.json"


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _walk(reader: Any, items: list, level: int, out: list[dict[str, Any]]) -> None:
    for it in items:
        if isinstance(it, list):
            _walk(reader, it, level + 1, out)
            continue
        try:
            page = reader.get_destination_page_number(it) + 1
        except Exception:
            page = None
        top = _num(getattr(it, "top", None)) if getattr(it, "typ", None) in ("/XYZ", "/FitH", "/FitBH") else None
        out.append({"level": level, "title": str(it.title), "page": page, "top": top})


def extract(name: str) -> list[dict[str, Any]]:
    import pypdf

    reader = pypdf.PdfReader(registry.get(name).pdf_path)
    out: list[dict[str, Any]] = []
    _walk(reader, reader.outline, 1, out)
    return out


def build_cache() -> dict[str, list[dict[str, Any]]]:
    rows = {}
    for d in registry.docs():
        if pdfmeta.outline(d.name):
            rows[d.name] = extract(d.name)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(rows, ensure_ascii=False, indent=0))
    return rows


@lru_cache(maxsize=1)
def _cached() -> dict[str, list[dict[str, Any]]]:
    if not CACHE.exists():
        return build_cache()
    return json.loads(CACHE.read_text())


def outline(name: str) -> list[dict[str, Any]]:
    """Bookmarks in document order: {level, title, page (1-based or None), top (PDF y or None)}."""
    return _cached().get(name, [])
