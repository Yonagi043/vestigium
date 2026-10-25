"""Calls of a session log (Claude Code session.jsonl of an evaluation run), normalized from the returned text.

The log keeps what the model saw, so signals and the tool's own additions are read back from the text here;
the server's raw events report them directly (events.py). What the text cannot show is left out: the M6
page growth and the zoom the tool chose are not marked in the text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .build import build
from .model import Call, Trace

TOOLS = {"list_documents", "outline", "search", "read", "context", "region"}
ADJACENT_NOTE = "[adjacent pages that match your searches]"
SATURATION_TEXT = "most hits were listed before"
NEW_LINE_CHARS = 30

_HIT = re.compile(r"^(\d+)\. (.+?) · p(\d+)(?:-(\d+))?(.*)$", re.M)
_PAGE_HIT = re.compile(r"^(\d+)\. p(\d+)\b(.*)$")
_SEC = re.compile(r"· (s\d+) ")
_ADDR = re.compile(r"\b(?:s|t|tab|fig)\d+\b")
_PAGE = re.compile(r"^== p(\d+)\b|\[p(\d+)\]", re.M)
_SCAN = re.compile(r"^== p(\d+)\b[^\n]*scanned", re.M)
_QC = re.compile(r"^\[ingest QC flags p(\d+): ([^\]]*)\]", re.M)
_CONT = re.compile(r"^continues: p(\d+)(?: \((s\d+)\))?", re.M)
_FIG = re.compile(r"^\[(fig\d+)\b", re.M)
_TAB = re.compile(r"^\[(tab\d+)\b", re.M)
_HEAD = re.compile(r"^## (s\d+) ·", re.M)
_NOTE_ADDR = re.compile(r"^\[note\] (.+?): (.*)$", re.M)  # an address the read could not take, and why
_TOO_LONG = re.compile(r"^\[note\] (\d+) addresses, about", re.M)
_EXACT = re.compile(r" · exact text: (\d+) matches")
_LITERAL_HEAD = re.compile(r"(\d+) matches(?: in (\d+) blocks)?")
_IDENT = re.compile(r'^literal "(.+?)": (\d+) matches', re.M)
_IDENT_HIT = re.compile(r"^  (\S+) · p(\d+)", re.M)
_CITE = re.compile(r"\(pp?\.\s*(\d+)")
_CONTEXT_HEAD = re.compile(r"^context \S+ · p(\d+)")
_OPENS = re.compile(r"^parent s\d+ opens \(p(\d+)")
_NOTE = re.compile(r"^  (?:\[[^\]]*\] )?\S+ p(\d+)\b")
_REGION_HEAD = re.compile(r"\bp(\d+)\b")


def _text(content: Any) -> tuple[str, int]:
    if isinstance(content, str):
        text, images = content, 0
    else:
        text = "\n".join(x.get("text", "") for x in content if x.get("type") == "text")
        images = sum(1 for x in content if x.get("type") == "image")
    if text.startswith('{"result"'):
        try:
            text = json.loads(text)["result"]
        except (ValueError, KeyError, TypeError):
            pass
    return text, images


def _tool(name: str) -> str | None:
    """'mcp__<server>__read' -> 'read' for this server's tools; other tools -> None."""
    short = name.rsplit("__", 1)[-1]
    return short if name.startswith("mcp__") and short in TOOLS else None


def _addrs(tool: str, args: dict[str, Any]) -> list[str]:
    key = {"read": "addrs", "context": "addr", "region": "target", "outline": "section"}.get(tool)
    value = args.get(key) if key else None
    if value is None:
        return []
    return [str(x).strip() for x in (value if isinstance(value, list) else [value])]


def _hits(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        if m := _PAGE_HIT.match(line):  # "3. p46 (printed 40) · 3× · s51 … | …": a page, not a block
            sec = _SEC.search(" " + m.group(3).split(" | ", 1)[0] + " ")
            out.append({"r": int(m.group(1)), "a": [], "p": [int(m.group(2))], "sec": sec.group(1) if sec else None})
        elif m := _HIT.match(line):
            lo, hi = int(m.group(3)), int(m.group(4) or m.group(3))
            sec = _SEC.search(" " + m.group(5))
            out.append({"r": int(m.group(1)), "a": [x for x in m.group(2).split() if not x.startswith("+")],
                        "p": list(range(lo, hi + 1)), "sec": sec.group(1) if sec else None})
    return out


def _search(c: Call, text: str, count_tokens: Any) -> None:
    head = text.split("\n", 1)[0]
    c.hits = _hits(text)
    if c.block == "grep":
        m = _LITERAL_HEAD.search(head)
        c.count = int(m.group(1)) if m else len(c.hits)
    elif m := _EXACT.search(head):
        c.count = int(m.group(1))
        c.decisions.append({"k": "exact_note", "by": "tool", "src": "exact", "n": c.count,
                            "tokens": count_tokens(m.group(0))})
    for key, n in _IDENT.findall(text):
        block = text[text.index(f'literal "{key}"'):].split("\n\n", 1)[0]
        c.decisions.append({"k": "ident_hits", "by": "tool", "src": "ident_hits", "keys": [key], "n": int(n),
                            "tokens": count_tokens(block)})
        c.hits += [{"r": 0, "a": [a], "p": [int(p)], "sec": None, "ident": key}
                   for a, p in _IDENT_HIT.findall(block)]
    if SATURATION_TEXT in text:
        c.signals.append({"k": "saturated"})
        line = next(x for x in text.splitlines() if SATURATION_TEXT in x)
        c.decisions.append({"k": "saturation_note", "by": "tool", "src": "saturation_hint", "tokens": count_tokens(line)})
    if (seen := text.count("| (seen)")):
        c.decisions.append({"k": "seen_marks", "by": "tool", "src": "seen", "n": seen})
    if not c.hits or (c.block == "grep" and c.count == 0):  # a ranked search with hits is not zero, whatever its count
        c.signals.append({"k": "zero"})


def _read(c: Call, text: str, count_tokens: Any) -> None:
    c.pages = sorted({int(a or b) for a, b in _PAGE.findall(text)})
    if c.tool == "context" and (m := _CONTEXT_HEAD.match(text)):  # its address's page, and the pages it quotes
        c.pages = sorted(set(c.pages) | {int(m.group(1))} | _context_pages(text))
        c.at = [int(m.group(1))]
        c.listed = list(dict.fromkeys(_ADDR.findall(text)))
    c.units = list(dict.fromkeys(_HEAD.findall(text) + _FIG.findall(text) + _TAB.findall(text)))
    for p, sec in _CONT.findall(text):
        c.signals.append({"k": "continues", "page": int(p), "sec": sec or None})
    lines = text.rstrip().splitlines()
    if lines and lines[-1].startswith("next: "):
        c.signals.append({"k": "cut", "rest": [a.strip() for a in lines[-1][len("next: "):].split(",") if a.strip()],
                          "why": "budget"})
    if "\n[cut: " in text:
        c.signals.append({"k": "cut", "rest": [], "why": "chars"})
    if m := _TOO_LONG.search(text):
        c.signals.append({"k": "too-long", "n_addrs": int(m.group(1))})
    c.addr_errors = [{"in": a, "err": why[:200]} for a, why in _NOTE_ADDR.findall(text)
                     if a in c.addrs and not why.startswith("from p")]  # "from p38, the page named with it" is no error
    if scans := sorted({int(p) for p in _SCAN.findall(text)}):
        c.signals.append({"k": "scan", "pages": scans})
    if qc := _QC.findall(text):
        c.signals.append({"k": "qc", "pages": sorted({int(p) for p, _ in qc})})
        c.decisions.append({"k": "qc_notes", "by": "tool", "src": "qc_notes",
                            "tokens": sum(count_tokens(f"[ingest QC flags p{p}: {w}]") for p, w in qc)})
    if figs := _FIG.findall(text):
        c.signals.append({"k": "figure", "units": list(dict.fromkeys(figs))})
    if tabs := _TAB.findall(text):
        c.signals.append({"k": "table", "units": list(dict.fromkeys(tabs))})
    if ADJACENT_NOTE in text:
        extra = text.split(ADJACENT_NOTE, 1)[1]
        c.decisions.append({"k": "adjacent", "by": "tool", "src": "adjacent_pages",
                            "pages": sorted({int(a or b) for a, b in _PAGE.findall(extra)}),
                            "tokens": count_tokens(ADJACENT_NOTE + extra)})


def _context_pages(text: str) -> set[int]:
    """The pages of the text a context return quotes: the parent's opening and the footnotes (reference lines
    and terms only point)."""
    pages, notes = set(), False
    for line in text.splitlines():
        if not line.startswith("  "):
            notes = line.startswith("footnotes (")
            if m := _OPENS.match(line):
                pages.add(int(m.group(1)))
        elif notes and (m := _NOTE.match(line)):
            pages.add(int(m.group(1)))
    return pages


def _normalize(c: Call, text: str, count_tokens: Any) -> None:
    c.tokens = count_tokens(text)
    if text.startswith("Error"):
        c.error = text.split("\n", 1)[0][:200]
    if c.tool == "search":
        _search(c, text, count_tokens)
    elif c.tool in ("read", "context"):
        _read(c, text, count_tokens)
    elif c.tool == "region":
        head = text.split("\n", 1)[0]
        if c.block == "view":
            m = _REGION_HEAD.search(head)
            c.pages = [int(m.group(1))] if m else []
        else:
            c.listed = list(dict.fromkeys(_ADDR.findall(text)))
    elif c.tool == "outline":
        c.listed = list(dict.fromkeys(_ADDR.findall(text)))


def _lines(text: str) -> set[str]:
    return {x for x in (" ".join(y.split()) for y in text.splitlines())
            if len(x) > NEW_LINE_CHARS and not x.startswith(("==", "## ", "next:", "continues:"))}


def calls_of(path: Path | str) -> tuple[list[Call], dict[str, Any] | None]:
    """The run's tool calls on this server (main conversation only) and its last answer text."""
    from tglw.common.text import count_tokens

    uses: dict[str, tuple[Call, str]] = {}
    calls: list[Call] = []
    turns: dict[str, int] = {}
    last_text: list[str] = []
    for raw in Path(path).read_text().splitlines():
        try:
            o = json.loads(raw)
        except json.JSONDecodeError:
            continue
        m = o.get("message")
        if o.get("isSidechain") or not isinstance(m, dict) or not isinstance(m.get("content"), list):
            continue
        for item in m["content"]:
            kind = item.get("type")
            if kind == "tool_use" and o.get("type") == "assistant":
                tool = _tool(item.get("name", ""))
                if tool:
                    turn = turns.setdefault(m.get("id") or o.get("uuid", ""), len(turns) + 1)
                    args = dict(item.get("input") or {})
                    c = Call(n=len(calls) + 1, turn=turn, tool=tool, args=args, doc=args.get("doc"),
                             addrs=_addrs(tool, args))
                    calls.append(c)
                    uses[item["id"]] = (c, tool)
                last_text = []
            elif kind == "tool_result" and item.get("tool_use_id") in uses:
                c, _ = uses[item["tool_use_id"]]
                text, c.images = _text(item.get("content", ""))
                if item.get("is_error") and not text.startswith("Error"):
                    text = "Error: " + text
                _normalize(c, text, count_tokens)
                c.__dict__["_text"] = text
            elif kind == "text" and o.get("type") == "assistant":
                last_text.append(item.get("text", ""))
    seen: set[str] = set()
    turn, pending = None, set()
    for c in calls:
        if c.turn != turn:
            seen |= pending
            pending, turn = set(), c.turn
        lines = _lines(c.__dict__.pop("_text", ""))
        if c.tool in ("read", "context") and lines:
            c.novelty = round(len(lines - seen) / len(lines), 3)
        pending |= lines
    answer_text = "\n".join(last_text).strip()
    answer = {"chars": len(answer_text), "cites": sorted({int(x) for x in _CITE.findall(answer_text)})} \
        if answer_text else None
    return calls, answer


def trace_of(path: Path | str) -> Trace:
    calls, answer = calls_of(path)
    return build(calls, "session", "session", answer)
