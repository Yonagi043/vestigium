"""The MCP server (the package command's `mcp` subcommand, or python -m tglw.mcp_server): list_documents, outline,
read, context, search, region -- over stdio, or with --http as a resident server (MCP streamable HTTP) that loads its documents before the first call. The resident
server talks the initialize handshake (MCP up to 2025-11-25), so every client conversation is an MCP session.

Returns are plain text (no structured-output wrapper). Sizes are capped so that Claude Code never
moves a result into a file: read and context at READ_CAP / CONTEXT_CAP tokens and CHAR_CAP characters.
Clients that move smaller results into files get them in parts or pages (see _limits).
With TGL_ALLOWED_DOCS set (comma-separated registered names), only those documents are listed and opened.

M8 switches (each can be turned off on its own; session state only adds to what is returned):
- limits_in_descriptions: the tool descriptions state "k ≤ 10" and "max_tokens ≤ 8000" (models copied them);
- adjacent_pages: a read also brings the page just before or after what it shows when that page ranks in the
  top ADJACENT_TOP pages for a search of this session; m6_next_page keeps M6's next page for small block
  reads alongside it (replacing it lost evidence M6 delivers);
- seen: a search hit already listed in the session with the same excerpt is shown without it, marked "seen";
- lenient_params: a string where a list is expected is a one-item list, null takes the default;
- saturation_hint: a search most of whose hits were listed before ends with a pointer to literal search.

V1 switches (task V1-INT; set in the environment before the server starts):
- limits (TGLW_MCP_LIMITS = auto | off | vscode | copilot): output limits by client, auto = by the name the
  client gives when it connects; off = Claude Code's for every client;
- answer_hint (TGLW_MCP_ANSWER_HINT=1): the server instructions end with AGENT P4's line on answering item by
  item (off by default, to be tried on development questions).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any

from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from pydantic import BeforeValidator

from tglw import COMMAND, NAME, PACKAGE, views
from tglw.views.switches import switch
from tglw.views.context import context as view_context

from tglw.common.text import count_tokens

from tglw import figures

from . import tools
from tglw.figures import region as figures_region

READ_CAP = 8000
CONTEXT_CAP = 4000
CHAR_CAP = 40_000
READ_CHARS = CHAR_CAP - 2_000  # room for the "next:" line
SEARCH_K_CAP = 10
ADJACENT_TOP = 5  # a neighbouring page is added when it is among this many pages for a search of the session
SATURATED = 0.5  # share of a search's hits listed before at which it points to literal search
M8 = {"limits_in_descriptions": False, "adjacent_pages": True, "m6_next_page": True, "seen": False,
      "lenient_params": True, "saturation_hint": True}
V1 = {"limits": os.environ.get("TGLW_MCP_LIMITS", "").strip().lower() or "auto",
      "answer_hint": switch("TGLW_MCP_ANSWER_HINT", False), "library": switch("TGLW_SEARCH_LIBRARY", False),
      "doc_addresses": switch("TGLW_READ_DOC_ADDR", False), "list": switch("TGLW_SEARCH_LIST", False),
      "qc_notes": switch("TGLW_ING_QC_NOTES", True)}

INSTRUCTIONS = (
    "Read long PDFs by address: outline(doc), search(doc, query), read(doc, addrs) several at once, "
    "context(doc, addr). Does the document state X at all? search mode='literal'. Cite p<N> (PDF page). "
    "region() returns images: use sparingly."
)
ANSWER_HINT = "Answer item by item: every item, condition and date the text gives, one per line, each with (p. N)."


def instructions() -> str:
    """The server instructions: the region sentence as task VIS's TOKEN_NOTE has it, then the answer hint (C5)."""
    return figures.cost.instructions(INSTRUCTIONS) + (" " + ANSWER_HINT if V1["answer_hint"] else "")


# serverInfo.name; Claude Code names the tools (mcp__<key>__read) and the instructions block by its own config key
server = MCPServer(PACKAGE, instructions=instructions())
_HIT = re.compile(r"^(\d+\. )(\S+)(.*?)( \| .*)?$")
_PAGE_MARK = re.compile(r"^== p(\d+)\b|\s\[p(\d+)\]\s", re.M)
ADJACENT_NOTE = "[adjacent pages that match your searches]"
SATURATION_NOTE = "most hits were listed before; whether the document states something at all: mode='literal' counts every match"


@dataclass
class Session:
    """What one conversation was shown, per document: hits with their excerpt ("seen"), hit addresses
    (saturation), its semantic queries and the pages delivered by read or context (adjacent pages)."""
    seen: dict[str, set[str]] = field(default_factory=dict)
    listed: dict[str, set[str]] = field(default_factory=dict)
    queries: dict[str, list[str]] = field(default_factory=dict)
    pages: dict[str, set[int]] = field(default_factory=dict)


HTTP_SESSIONS = 256  # conversations a resident server remembers (least recently used dropped)
_process = Session()  # stdio: the process serves one conversation
_sessions: OrderedDict[str, Session] = OrderedDict()
_sessions_lock = threading.Lock()
_http = False  # set by main(--http)


def reset_session() -> None:
    global _process
    _process = Session()
    with _sessions_lock:
        _sessions.clear()


def _session(ctx: Context | None) -> Session:
    """The conversation a call belongs to: VS Code's conversation id when the call carries one (one VS Code
    window shares a server between its chats), else under --http the MCP session; a stdio server is one
    conversation, and an HTTP call with neither id (2026-07-28, with --new-protocol) gets a fresh one."""
    if ctx is None:
        return _process
    meta = ctx.request_context.meta or {}
    key = meta.get("vscode.conversationId") or (_http and (ctx.headers or {}).get("mcp-session-id"))
    if not key:
        return Session() if _http else _process
    with _sessions_lock:
        s = _sessions.pop(str(key), None) or Session()
        _sessions[str(key)] = s
        while len(_sessions) > HTTP_SESSIONS:
            _sessions.popitem(last=False)
    return s


@dataclass(frozen=True)
class Limits:
    """A client's output limits: `part` for each text part (UTF-16 code units; a longer result goes out in
    several parts), `total` for the whole result (UTF-8 bytes; reads and context stop below it)."""
    part: int | None = None
    total: int | None = None


LIMITS = {"off": Limits(), "vscode": Limits(part=8_000), "copilot": Limits(total=18_000)}
CLIENTS = ((re.compile(r"visual studio code|code - oss|vscodium", re.I), "vscode"), (re.compile(r"copilot", re.I), "copilot"))


def _limits(ctx: Context | None) -> Limits:
    """VS Code's chat writes a text part over 8,192 characters to a file and shows the model a pointer,
    checking each part on its own (chat.agent.largeToolResultsToDisk, in its source); Copilot CLI saves tool
    output over 20 KiB to a file with a preview (its docs). Claude Code and other clients keep CHAR_CAP."""
    mode = V1["limits"]
    if mode != "auto":
        return LIMITS.get(mode, LIMITS["off"])
    params = ctx.session.client_params if ctx is not None else None
    name = params.client_info.name if params is not None else ""
    return next((LIMITS[key] for pattern, key in CLIENTS if pattern.search(name)), LIMITS["off"])


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _parts(text: str, size: int) -> list[str]:
    """Text in parts of at most `size` UTF-16 code units, cut at line ends (a longer line at spaces)."""
    lines: list[str] = []
    for line in text.split("\n"):
        while _utf16(line) > size:
            limit = size if _utf16(line[:size]) <= size else size // 2  # characters beyond U+FFFF count twice
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            lines.append(line[:cut])
            line = line[cut:].lstrip(" ")
        lines.append(line)
    parts, cur, used = [], [], 0
    for line in lines:
        n = _utf16(line) + (1 if cur else 0)
        if cur and used + n > size:
            parts.append("\n".join(cur))
            cur, used, n = [], 0, _utf16(line)
        cur.append(line)
        used += n
    return parts + ["\n".join(cur)]


def _send(text: str, lim: Limits) -> str | list[str]:
    """A text result as the client takes it: cut below CHAR_CAP (and the client's total), in parts when
    the client limits each part."""
    text = _fit(text, lim)
    if lim.part is None or _utf16(text) <= lim.part:
        return text
    return _parts(text, lim.part)


def _shrink(make: Callable[[int], str], budget: int, lim: Limits, floor: int) -> str:
    """make(budget), with the budget lowered until the result fits the client's total bytes."""
    text = make(budget)
    while lim.total is not None and (size := len(text.encode())) > lim.total - 200 and budget > floor:
        budget = max(floor, budget * (lim.total - 200) // size)
        text = make(budget)
    return text


def _lenient_list(value: Any) -> Any:
    return [value] if M8["lenient_params"] and isinstance(value, str) else value


def _or(default: Any) -> BeforeValidator:
    """null -> the parameter's default (the JSON schema stays the same)."""
    return BeforeValidator(lambda value: default if value is None and M8["lenient_params"] else value)


def _description(tool: str) -> str:
    if tool == "read":
        return ("Read addresses: s12 p45 p45-47 t123 tab4 (whole table) fig5, printed P36 or P3@Exhibit 10.21, "
                "'Note 7' '§2.2' 'AC-2(3)'. " + ("max_tokens ≤ 8000; a" if M8["limits_in_descriptions"] else "A")
                + " cut read ends with 'next: …'." + (" doc:address reads another document." if V1["doc_addresses"] else ""))
    return ("Search one document: ≥3 hits" + (" (k ≤ 10)" if M8["limits_in_descriptions"] else "")
            + ", address · page · section | hit sentence" + ("; hits shown before are marked seen" if M8["seen"] else "")
            + ". unit: block|page. mode='literal': exact text, counts all matches."
            + (" doc='*': every document, hits as doc:address." if V1["library"] else "")
            + (" mode='list': every exact match as addresses (offset: from there on)." if V1["list"] else ""))


def render_result(text: str) -> str:
    """What the client receives for a text result (plain text since M3)."""
    return text


def _doc(doc: str) -> str:
    _refresh()
    try:
        return tools.resolve_doc(doc)
    except tools.LookupFailed as err:
        raise ToolError(str(err)) from err


_library = [tools.registry.stamp()]  # the library the caches were filled from


def _refresh() -> None:
    """After the ingest command added, re-ingested or removed a document (registry.stamp changes once it has written
    everything), forget the registry and every cached view, index and list, so a server that keeps running
    serves the library as it is now."""
    now = tools.registry.stamp()
    if now == _library[0]:
        return
    _library[0] = now
    tools.registry.reload()
    from tglw.common import bundle
    from tglw.search import index as g_index
    from tglw.views import api, docview, sidecars
    for cache in (tools.doc_index, api.view, api._index, api._block_tokens, sidecars.delivered, docview._precomputed,
                  _top_pages, bundle._shared, g_index.load):
        cache.cache_clear()


def _mark_seen(name: str, text: str, s: Session) -> str:
    """A hit whose address and excerpt were both shown before in this session keeps only its address;
    the same block with another excerpt (another query) is shown again."""
    if not M8["seen"]:
        return text
    seen = s.seen.setdefault(name, set())
    out = []
    for line in text.splitlines():
        m = _HIT.match(line)
        if m and m.group(4) and not line.startswith("section number"):
            key = f"{m.group(2)}{m.group(4)}"
            if key in seen:
                line = f"{m.group(1)}{m.group(2)}{m.group(3)} | (seen)"
            seen.add(key)
        out.append(line)
    return "\n".join(out)


def _fit(text: str, lim: Limits | None = None) -> str:
    """Last guard on size: cut at a line boundary below CHAR_CAP characters (and the client's total bytes)."""
    cap = CHAR_CAP
    if lim is not None and lim.total is not None and len(text.encode()) > lim.total:
        cap = len(text.encode()[:lim.total].decode(errors="ignore"))
    if len(text) <= cap:
        return text
    cut = text.rfind("\n", 0, cap - 200)
    return text[:cut] + "\n[cut: output too long for one call; ask for fewer addresses or a smaller max_tokens]"


@server.tool(description="List documents (only needed when no document is named): name, pages, tokens, structure.",
             structured_output=False)
def list_documents(filter: Annotated[str, _or("")] = "", ctx: Context | None = None) -> str | list[str]:
    _refresh()
    try:
        return _send(tools.list_documents(filter, qc=V1["qc_notes"]), _limits(ctx))
    except tools.LookupFailed as err:
        raise ToolError(str(err)) from err


@server.tool(description="Section outline: id, title, PDF pages (printed), tokens. section: expand one id.",
             structured_output=False)
def outline(doc: str, depth: Annotated[int, _or(2)] = 2, section: str | None = None,
            ctx: Context | None = None) -> str | list[str]:
    name = _doc(doc)
    text = figures.annotate.outline(name, views.outline(name, depth=depth, section=section))
    return _send(_qc_header(name, text), _limits(ctx))


def _qc_header(name: str, text: str) -> str:
    """For a document with an ingest quality report (task ING), the outline's first line ends with its verdict
    instead of the sidecar versions."""
    if not V1["qc_notes"]:
        return text
    from tglw.ingest import qc
    verdict = qc.summary(name)
    if verdict is None:
        return text
    head, sep, rest = text.partition("\n")
    return re.sub(r" · sidecars: .*$", f" · {verdict}", head) + sep + rest


@server.tool(description=_description("read"), structured_output=False)
def read(doc: str, addrs: Annotated[list[str], BeforeValidator(_lenient_list)],
         max_tokens: Annotated[int, _or(READ_CAP)] = READ_CAP, ctx: Context | None = None) -> str | list[str]:
    runs = _runs(doc, addrs)
    s, lim = _session(ctx), _limits(ctx)
    budget = max(200, min(max_tokens, READ_CAP))
    chars = READ_CHARS if lim.total is None else min(READ_CHARS, lim.total - 2_000)
    if len(runs) == 1:
        name, run = runs[0]
        text = _shrink(lambda c: _read_one(name, run, budget, c, s), chars, lim, 2_000)
        s.pages.setdefault(name, set()).update(_delivered(text))
    else:
        text = _shrink(lambda c: _read_runs(runs, budget, c, s), chars, lim, 2_000)
        for name, part in _parts_by_doc(text):
            s.pages.setdefault(name, set()).update(_delivered(part))
    return _send(text, lim)


def _read_one(name: str, addrs: list[str], budget: int, chars: int, s: Session) -> str:
    text = views.read(name, addrs, max_tokens=budget, max_chars=chars, grow=M8["m6_next_page"] or not M8["adjacent_pages"])
    text = _adjacent(name, text, budget, s, chars) if M8["adjacent_pages"] else text
    with tools.PDFIUM:  # task VIS: figure lines, drawing hints, scanned-page marks
        text = figures.annotate.read(name, text, addrs)
    return _qc_pages(name, text)


def _qc_pages(name: str, text: str) -> str:
    """Under each page the ingest quality check distrusts, why (task ING; documents without a report, such as
    the research corpus, are left as they are)."""
    if not V1["qc_notes"]:
        return text
    from tglw.ingest import qc
    report = qc.load(name)
    suspects = {x["page"]: x["why"] for x in (report or {}).get("suspects", [])}
    if not suspects:
        return text
    out = []
    for line in text.split("\n"):
        out.append(line)
        if (m := _PAGE_HEAD.match(line)) and int(m.group(1)) in suspects:
            out.append(f"[ingest QC flags p{m.group(1)}: {'; '.join(suspects[int(m.group(1))])}]")
    return "\n".join(out)


_PAGE_HEAD = re.compile(r"^== p(\d+)\b")


DOC_HEAD = "== doc: {} =="


def _runs(doc: str, addrs: list[str]) -> list[tuple[str, list[str]]]:
    """The addresses by document: all in `doc`, or with V1["doc_addresses"] (task SRCH) runs of "doc:address"
    in the documents they name, the rest in `doc` ("*": none)."""
    if not V1["doc_addresses"]:
        return [(_doc(doc), list(addrs))]
    from tglw import corpus
    _refresh()
    try:
        return corpus.group_addrs(addrs, default=None if doc.strip() == "*" else _doc(doc), names=tools.allowed_docs())
    except corpus.DocAddrError as err:
        raise ToolError(str(err)) from err


def _read_runs(runs: list[tuple[str, list[str]]], budget: int, chars: int, s: Session) -> str:
    """Several documents read in turn within one budget, each under "== doc: <name> =="; a cut read ends
    with next: addresses that name their documents."""
    out: list[str] = []
    used = used_chars = 0
    for i, (name, addrs) in enumerate(runs):
        head = DOC_HEAD.format(name)
        if budget - used < 200 or chars - used_chars < 2_000:
            out.append("next: " + ", ".join(f"{n}:{a}" for n, run in runs[i:] for a in run))
            break
        text = _read_one(name, addrs, budget - used, chars - used_chars - len(head) - 1, s)
        lines = text.split("\n")
        if lines[-1].startswith("next: "):
            rest = [f"{name}:{a}" for a in lines[-1][len("next: "):].split(", ")]
            out += [head, *lines[:-1], "next: " + ", ".join(rest + [f"{n}:{a}" for n, run in runs[i + 1:] for a in run])]
            break
        out += [head, *lines]
        used += count_tokens(text)
        used_chars += len(text) + len(head) + 2
    return "\n".join(out)


def _parts_by_doc(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        if line.startswith("== doc: ") and line.endswith(" =="):
            out.append((line[len("== doc: "):-3], []))
        elif out:
            out[-1][1].append(line)
    return [(name, "\n".join(lines)) for name, lines in out]


def _one_doc(doc: str, addr: str) -> tuple[str, str]:
    """(document, address) for context and region: "doc:address" names its document under V1["doc_addresses"]."""
    if V1["doc_addresses"]:
        from tglw import corpus
        _refresh()
        try:
            named, rest = corpus.split_doc_addr(addr, tools.allowed_docs(), strict=doc.strip() == "*")
        except corpus.DocAddrError as err:
            raise ToolError(str(err)) from err
        if named:
            return named, rest
    return _doc(doc), addr


def _delivered(text: str) -> set[int]:
    return {int(a or b) for a, b in _PAGE_MARK.findall(text)}


@lru_cache(maxsize=512)
def _top_pages(name: str, query: str) -> frozenset[int]:
    from tglw.search import search as g_search
    return frozenset(h.page for h in g_search(name, query, k=ADJACENT_TOP, unit="page"))


def _adjacent(name: str, text: str, budget: int, s: Session, max_chars: int = READ_CHARS) -> str:
    """The page just before or after each run of pages the read shows, when the session has not had it
    and it ranks in the top ADJACENT_TOP pages for one of the session's searches (IR P1: of the
    evidence the model missed, 6 of 8 cases were on the page next to one it read). Whole pages only,
    within the read's own token and character budget; none after a cut read (its next: goes on)."""
    lines = text.splitlines()
    got = _delivered(text)
    if not got or not s.queries.get(name) or (lines and lines[-1].startswith("next: ")):
        return text
    pool = frozenset().union(*(_top_pages(name, q) for q in s.queries[name]))
    runs: list[list[int]] = []
    for p in sorted(got):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    last = views.view(name).doc.page_count
    shown = got | s.pages.get(name, set())
    cand = [p for p in dict.fromkeys([b + 1 for _, b in runs] + [a - 1 for a, _ in runs])
            if 1 <= p <= last and p not in shown and p in pool]
    used, chars, extra = count_tokens(text), len(text), []
    for p in sorted(cand):
        page = views.read(name, [f"p{p}"], max_tokens=max(1, budget - used), max_chars=max_chars - chars, grow=False)
        if not page.strip() or page.splitlines()[-1].startswith("next: ") or page.startswith("[note]"):
            continue  # does not fit whole
        extra.append(page)
        used += count_tokens(page)
        chars += len(page) + 1
    if not extra:
        return text
    added = _delivered("\n".join(extra))
    if (m := re.match(r"continues: p(\d+) ", lines[-1])) and int(m.group(1)) in added:
        lines = lines[:-1]  # the page it points to follows
    return "\n".join(lines + [ADJACENT_NOTE] + extra)


@server.tool(description="Context of an address: section path, table parts, parent opening, siblings, references "
                         "in and out, terms, footnotes.",
             structured_output=False)
def context(doc: str, addr: str, max_tokens: Annotated[int, _or(1500)] = 1500, part: str | None = None,
            ctx: Context | None = None) -> str | list[str]:
    name, addr = _one_doc(doc, addr)
    s, lim = _session(ctx), _limits(ctx)
    text = _shrink(lambda tokens: view_context(name, addr, max_tokens=tokens, part=part),
                   max(200, min(max_tokens, CONTEXT_CAP)), lim, 200)
    s.pages.setdefault(name, set()).update(_delivered(text))
    return _send(text, lim)


@server.tool(description=_description("search"), structured_output=False)
def search(doc: str, query: str, k: Annotated[int, _or(5)] = 5, unit: Annotated[str, _or("block")] = "block",
           mode: Annotated[str, _or("bm25")] = "bm25", offset: Annotated[int, _or(0)] = 0,
           ctx: Context | None = None) -> str | list[str]:
    if unit not in ("block", "page"):
        raise ToolError("unit must be 'block' or 'page'")
    if mode not in ("bm25", "literal") and not (mode == "list" and V1["list"]):
        raise ToolError("mode must be 'bm25' or 'literal'" + (" or 'list'" if V1["list"] else ""))
    s = _session(ctx)
    if doc.strip() == "*" and V1["library"]:
        return _send(_library_search(query, min(k, SEARCH_K_CAP), unit, mode, s), _limits(ctx))
    name = _doc(doc)
    if mode == "list":  # task SRCH: every exact match as addresses, grouped by section
        from tglw.corpus.listing import list_literal
        return _send(list_literal(name, query, offset=max(0, offset)), _limits(ctx))
    text = views.search(name, query, k=min(k, SEARCH_K_CAP), unit=unit, mode=mode)
    if mode == "bm25":
        s.queries.setdefault(name, []).append(query)
        text = _saturation(name, text, s)
    return _send(_mark_seen(name, text, s), _limits(ctx))


def _library_search(query: str, k: int, unit: str, mode: str, s: Session) -> str:
    """Every document's passages at once (task SRCH's library index); its query counts as a search of each
    document it found, for the adjacent pages of later reads."""
    if mode != "bm25":
        raise ToolError("literal search needs a document: search(doc=<name>, query, mode='literal')")
    from tglw import corpus
    _refresh()
    try:
        text = corpus.search_text(query, k=k, unit=unit, docs=tools.allowed_docs())
    except (FileNotFoundError, RuntimeError) as err:
        raise ToolError(str(err)) from err
    for name in dict.fromkeys(m.group(1) for m in _LIBRARY_HIT.finditer(text)):
        s.queries.setdefault(name, []).append(query)
    return text


_LIBRARY_HIT = re.compile(r"^\d+\. ([^:\s]+):", re.M)


def _saturation(name: str, text: str, s: Session) -> str:
    """A semantic search most of whose hits were listed by earlier searches ends with a pointer to literal
    search (AGENT P5: haiku rephrased up to 26 times on questions the document does not answer)."""
    hits = [m.group(2) for line in text.splitlines() if (m := _HIT.match(line)) and m.group(4)]
    listed = s.listed.setdefault(name, set())
    again = sum(h in listed for h in hits)
    listed.update(hits)
    if M8["saturation_hint"] and hits and again / len(hits) >= SATURATED:
        return text + "\n" + SATURATION_NOTE
    return text


@server.tool(description=figures.cost.region_description(
    "Image and text layer of tab4/fig5/t123 or box 'p45:l,t,r,b' (pt, origin bottom-left); "
    "'p45' whole page; preview=true: coordinates only. Images are costly."), structured_output=False)
def region(doc: str, target: str, scale: Annotated[float, _or(tools.REGION_SCALE)] = tools.REGION_SCALE,
           preview: Annotated[bool, _or(False)] = False, ctx: Context | None = None) -> Any:
    lim = _limits(ctx)
    name, target = _one_doc(doc, target)
    try:
        if preview:
            return _send(tools.preview(name, target), lim)
        auto = figures.switches.on("ADAPTIVE_ZOOM") and scale == tools.REGION_SCALE  # task VIS (E3): scale by the print
        with tools.PDFIUM:
            r = figures_region.region(name, target, scale=None if auto else min(max(scale, 0.5), 3.0))
    except tools.LookupFailed as err:
        raise ToolError(str(err)) from err
    text = _send(f"{r.caption}\n{r.text}", lim)
    return [*([text] if isinstance(text, str) else text), Image(data=r.png, format="png")]


def _strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


for _tool in server._tool_manager.list_tools():
    _tool.parameters = _strip_titles(_tool.parameters)
    if _tool.name == "search" and not V1["list"]:
        del _tool.parameters["properties"]["offset"]  # only mode='list' takes it: the schema stays as before


def preload(which: str) -> list[str]:
    """Parse the served documents and build their indexes before the first call (--http): "all" (in list order,
    as many as the document caches keep within TGLW_CACHE_MB), "none", or comma-separated names."""
    from tglw.common import bundle
    allowed = tools.allowed_docs()
    served = allowed if allowed is not None else [d.name for d in tools.registry.docs()]
    if which == "none":
        return []
    if which == "all":
        names, used = [], 0
        for name in served:
            used += bundle.DOCS.estimate(name)
            if names and used > bundle.DOCS.budget():
                break
            names.append(name)
    else:
        names = [tools.resolve_doc(n) for n in which.split(",") if n.strip()]
    for name in names:
        views.outline(name)
        views.search(name, "page", k=3)
        views.search(name, "page", k=3, unit="page")
        _top_pages(name, "page")
    return names


def handshake_only(app: Any) -> Any:
    """The HTTP app answering MCP 2026-07-28 requests as a server without that revision does (400), so clients
    connect with the initialize handshake and get an MCP session -- Claude Code 2.1.283 does so -- and what a
    conversation was shown (adjacent pages, the saturation hint) is kept per session, as a stdio server keeps it."""
    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        version = dict(scope.get("headers") or []).get(b"mcp-protocol-version", b"").decode("latin-1")
        if scope["type"] != "http" or not version or version in HANDSHAKE_PROTOCOL_VERSIONS:
            return await app(scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        try:
            rid = json.loads(body).get("id")
        except (ValueError, AttributeError):
            rid = None
        error = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": f"Bad Request: unsupported protocol version {version}"}}
        await send({"type": "http.response.start", "status": 400, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": json.dumps(error).encode()})
    return wrapped


def main(argv: list[str] | None = None) -> int | None:
    """`<command> mcp [options]`: argv are the arguments after the subcommand (tglw.cli)."""
    ap = argparse.ArgumentParser(prog=f"{COMMAND} mcp", description=f"{NAME} MCP server: stdio, or a resident HTTP server with --http.")
    ap.add_argument("--http", action="store_true", help="serve MCP streamable HTTP at http://HOST:PORT/mcp instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1: this machine only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--preload", default="all", help="documents to load before serving: all (as many as TGLW_CACHE_MB, "
                                                     "default 2048, holds), none, or comma-separated names")
    ap.add_argument("--new-protocol", action="store_true",
                    help="also take MCP 2026-07-28 requests; they carry no session, so for such clients nothing is kept "
                         "between calls (no adjacent pages, no saturation hint)")
    a = ap.parse_args(argv)
    if not a.http:
        server.run()
        return
    import uvicorn

    global _http
    _http = True
    t0 = time.perf_counter()
    names = preload(a.preload)
    app = server.streamable_http_app(host=a.host)
    print(f"{COMMAND} mcp: {len(names)} documents loaded in {time.perf_counter() - t0:.1f} s; "
          f"serving http://{a.host}:{a.port}/mcp", file=sys.stderr, flush=True)
    uvicorn.run(app if a.new_protocol else handshake_only(app), host=a.host, port=a.port, log_level="warning")
