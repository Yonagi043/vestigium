"""Raw definition records produced by the extractors, before merging into the sidecar."""

from __future__ import annotations

from dataclasses import dataclass, field

SOURCES = ("abbrev_table", "definitions_chapter", "glossary", "inline")


@dataclass(slots=True)
class Definition:
    term: str
    variants: list[str]
    definition: str
    ref: str
    page: int
    source: str
    section: str | None = None
    cite: str | None = None
    order: int = 0
    extra: dict = field(default_factory=dict)
