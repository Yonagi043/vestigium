"""PDF outlines (bookmarks) and page labels, precomputed by scripts/build_pdf_meta.py."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .paths import COMMON_DATA


@lru_cache(maxsize=1)
def _all() -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in json.loads((COMMON_DATA / "pdf_meta.json").read_text())}


def meta(name: str) -> dict[str, Any]:
    return _all()[name]


def outline(name: str) -> list[dict[str, Any]]:
    """Entries in document order: {"level": 1.., "title": str, "page": 1-based int | None}."""
    return meta(name)["outline"]


def page_labels(name: str) -> list[str] | None:
    """Printed labels per PDF page from /PageLabels, or None when the PDF has none."""
    return meta(name)["page_labels"]
