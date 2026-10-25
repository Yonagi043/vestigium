"""Traces in the reading language: one Step per tool call, built from normalized calls by `build`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Call:
    """One tool call with what it returned, normalized from a raw event or a session log (facts only)."""

    n: int                      # call order, from 1
    turn: int                   # calls sent together (one model turn) share a turn
    tool: str
    args: dict[str, Any]
    doc: str | None = None
    addrs: list[str] = field(default_factory=list)        # addresses asked for (read, context, region)
    addr_errors: list[dict[str, Any]] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)        # pages whose text or image came back
    at: list[int] = field(default_factory=list)           # context: its address's page (where it came from is judged by it)
    units: list[str] = field(default_factory=list)        # sections, tables, figures that came back
    refs: list[str] = field(default_factory=list)         # blocks that came back (raw events only)
    hits: list[dict[str, Any]] = field(default_factory=list)  # search hits: {"r", "a": [addr], "p": [page], "sec"}
    count: int | None = None    # exact matches (literal search, or the count the tool added to a search)
    listed: list[str] = field(default_factory=list)       # addresses a structure view listed
    signals: list[dict[str, Any]] = field(default_factory=list)    # {"k": ..., ...}: continues, cut, zero, ...
    decisions: list[dict[str, Any]] = field(default_factory=list)  # {"k", "by", "src", "tokens", ...}
    tokens: int = 0
    images: int = 0
    error: str | None = None
    novelty: float | None = None  # share of returned text lines not seen before (session logs only)
    t0: float | None = None       # start and end time (raw events only)
    t1: float | None = None

    @property
    def block(self) -> str:
        if self.tool == "search":
            return "grep" if self.args.get("mode") in ("literal", "list") else "find"
        if self.tool == "read":
            return "read"
        if self.tool == "region" and not self.args.get("preview") and self.images:
            return "view"
        return "map"  # outline, context, list_documents, region preview


@dataclass
class Step:
    call: Call
    label: str                  # "3", or "2a", "2b" for calls sent together
    origin: str | None = None   # hit, continues, cut, map, near, again, new; None for searches and outline
    origin_step: str | None = None
    origin_rank: int | None = None
    new_pages: list[int] = field(default_factory=list)


@dataclass
class Trace:
    source: str                 # "events" or "session"
    conv: str
    doc: str | None
    steps: list[Step]
    ignored: list[dict[str, Any]] = field(default_factory=list)   # {"k", "step", "target"}
    answer: dict[str, Any] | None = None  # session logs only: {"chars", "cites": [pages]}
    lost: dict[str, int] = field(default_factory=dict)            # reports the recorder could not attach
    rules: list[dict[str, Any]] = field(default_factory=list)     # rule tool calls: {"after": step label, "action", "text"}

    @property
    def pages(self) -> list[int]:
        return sorted({p for s in self.steps for p in s.call.pages if s.call.block in ("read", "map", "view")})

    @property
    def tokens(self) -> int:
        return sum(s.call.tokens for s in self.steps)
