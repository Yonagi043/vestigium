"""R4 `add absence`: a search that found nothing, turned into facts a reader can cite instead of searching
again (LANG_SPEC 11.4, "add absence" details). Only facts, never "the document does not mention X":

    "one-tenth": 0 exact matches in all 350 pages (body text, tables, text in figures; page headers and footers not searched)
    scanned pages, searched as OCR text: 3 (p1 p2 p3) · possible lost text on 2 pages (p210 p214)
    other spellings: "one tenth" 2 · p12 p40 | 0: "onetenth"
    nearest passages:
    1. t84 · p11 (printed 2010-1) · s2 … | …

`absence()` gives the lines with the parts the executor records (near passages, spellings tried, scope),
cut to a token budget in a fixed order; None when not even the first lines fit. `no_match()` is the zero
signal of a default search. Reads the document through the reader's view (tglw.views), so literal counts
are the reader's literal search, Chinese folding included.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, NamedTuple

from tglw.common import bundle
from tglw.common.text import count_tokens

from .api import Config, Hit, _near, search
from .index import load
from .tokens import _STOP, FOLD, tokens

ABSENCE_MAX = 300
NEAR_MAX = 3
VARIANTS_MAX = 6
PAGES_SHOWN = 3
KEEP_ZERO_VARIANTS = 3  # zero-count spellings after this many are the second thing cut
EXCERPT_WORDS = (25, 12)  # excerpts are shortened to these lengths, in turn, when the block does not fit
_ID = re.compile(r"\b([A-Z]{2,4})-(\d{1,2})((?:\(\d{1,2}\))*)")
_DOTTED = re.compile(r"(?<![\w.])\d{1,2}(?:\.\d{1,2}){1,3}(?![\w.])")
_PERCENT = re.compile(r"(\d)\s*(%|percent\b|per cent\b)", re.I)
PART_WORDS = 3  # a literal query longer than this has its parts counted (EXACT_CHECK_WORDS in views)
PARTS_MAX, PARTS_KEEP, RARE_WORDS = 6, 2, 2
_QUOTED = re.compile(r"[\"“”]([^\"“”]{2,80})[\"“”]")
_SECTION = re.compile(r"§\s?\d+(?:\.\d+)*")
_ACRONYM = re.compile(r"(?<![\w/&])[A-Z](?:[A-Z0-9]|[/&](?=[A-Z]))+s?(?![\w/&])")  # ASP, N/S, R&D, CMGs
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:\s*(?:thousand|million|billion|trillion|percent\b|%))?", re.I)


@dataclass
class Absence:
    lines: list[str]
    near: list[dict[str, Any]] = field(default_factory=list)  # {addr, page, score}
    variants: list[dict[str, Any]] = field(default_factory=list)  # {text, n, pages}
    parts: list[dict[str, Any]] = field(default_factory=list)  # {text, n}: whole-word counts of a long query's parts
    scope: dict[str, Any] = field(default_factory=dict)
    tokens: int = 0
    trimmed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def no_match(hits: Iterable[Hit]) -> bool:
    """The zero signal of a default search: no hit with a positive score after G's near-spelling retry
    (a page search lists 0-score pages when nothing matches; they count as none)."""
    return not any(h.score > 0 for h in hits)


# ---- scope -----------------------------------------------------------------------------------

@bundle.doc_cache
def _scanned(name: str) -> tuple[int, ...]:
    from tglw.common import registry
    from tglw.tables.pagecells import scanned_pages

    return tuple(sorted(scanned_pages(str(registry.get(name).pdf_path), set(range(1, bundle.shared(name).page_count + 1)))))


@bundle.doc_cache
def _empty(name: str) -> tuple[int, ...]:
    """Pages where the reader shows no text at all."""
    from tglw.views import api

    v = api.view(name)
    have = {seg.page for b in v.doc.blocks if b.kind in ("text", "table") and b.text.strip()
            for seg in v.block_segments(b) if seg.text.strip() and v.visible(b, seg.page)}
    return tuple(p for p in range(1, v.doc.page_count + 1) if p not in have)


def scope(v: Any) -> dict[str, Any]:
    """What a literal search of this document covers: its pages, the scanned ones (searched as OCR
    text), pages without any text, and pages task D1 flags as having lost text."""
    loss = (v.side.textloss or {}).get("pages") if v.side.textloss else None
    out: dict[str, Any] = {"pages": v.doc.page_count, "scanned": list(_scanned(v.name)), "empty": list(_empty(v.name))}
    if loss is not None:
        out["lost"] = [p["pdf_page"] for p in loss if p.get("flag") == "partial"]
    return out


def _pages(pages: list[int]) -> str:
    return " ".join(f"p{p}" for p in pages[:PAGES_SHOWN]) + (f" +{len(pages) - PAGES_SHOWN}" if len(pages) > PAGES_SHOWN else "")


def scope_lines(query: str, mode: str, sc: dict[str, Any], folded: bool) -> list[str]:
    what = "body text, tables, text in figures; page headers and footers not searched"
    if folded:
        what += "; traditional and simplified characters matched as one"
    head = (f'"{query}": 0 exact matches in all {sc["pages"]} pages ({what})' if mode == "literal" else
            f'"{query}": no passage scored above 0, near spellings included, in all {sc["pages"]} pages ({what})')
    facts = []
    if sc["scanned"]:
        facts.append(f"scanned pages, searched as OCR text: {len(sc['scanned'])} ({_pages(sc['scanned'])})")
    if sc["empty"]:
        facts.append(f"pages with no text: {len(sc['empty'])} ({_pages(sc['empty'])})")
    if sc.get("lost"):
        facts.append(f"possible lost text on {len(sc['lost'])} page{'s' if len(sc['lost']) > 1 else ''} ({_pages(sc['lost'])})")
    return [head] + ([" · ".join(facts)] if facts else [])


# ---- other spellings -------------------------------------------------------------------------

def _id_variant(text: str, pad: bool) -> str:
    """Control numbers and dotted numbers with leading zeros ("PT-07(02)", "03.10.03") or without."""
    def number(m: re.Match[str]) -> str:
        return f"{int(m.group(0)):02d}" if pad else str(int(m.group(0)))

    def control(m: re.Match[str]) -> str:
        return f"{m.group(1)}-{number(re.match(r'\d+', m.group(2)))}{re.sub(r'\d+', number, m.group(3))}"

    return _DOTTED.sub(lambda m: re.sub(r"\d+", number, m.group(0)), _ID.sub(control, text))


def _plural(word: str) -> str | None:
    w = word.lower()
    if len(w) < 3 or not w.isalpha():
        return None
    if w.endswith("ies") and len(w) > 4:
        return word[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return word[:-1]
    if w.endswith("y") and len(w) > 3 and w[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + ("es" if w.endswith(("s", "x", "ch", "sh")) else "s")


def spellings(query: str, name: str) -> list[str]:
    """Writings that change the string (literal search already ignores case, dash and quote kinds and
    thousands separators): a hyphen as a space or dropped (a two-word phrase hyphenated or joined), the
    last word's singular or plural, identifiers with
    and without leading zeros, "%" and "percent", and words the document lacks replaced by its words one
    letter away (OCR). In this order, at most VARIANTS_MAX."""
    q = " ".join(query.split())
    out: list[str] = []
    words = q.split(" ")
    if "-" in q:
        out += [q.replace("-", " "), q.replace("-", "")]
    elif len(words) == 2 and all(w.isalpha() for w in words):
        out += ["-".join(words), "".join(words)]
    if (p := _plural(words[-1])) is not None:
        out.append(" ".join(words[:-1] + [p]))
    out += [_id_variant(q, pad=True), _id_variant(q, pad=False)]
    out.append(_PERCENT.sub(lambda m: m.group(1) + (" percent" if m.group(2) == "%" else "%"), q))
    ix = load(name)
    for w in re.findall(r"[A-Za-z]{5,}", q):
        for near in _near(ix, w.lower(), Config())[:2]:
            out.append(re.sub(rf"\b{re.escape(w)}\b", near, q))
    seen = {q.lower()}
    uniq = []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return uniq[:VARIANTS_MAX]


def count_literal(v: Any, text: str, whole: bool = False) -> tuple[int, list[int]]:
    """(matches, pages) of the reader's literal search for `text`; `whole`: whole-word matches only."""
    from tglw.views import api

    with api._cjk_of(v):
        hits = [h for h in api._literal_hits_in(v, text) if h[0] or not whole]
    return sum(h[0] if whole else h[1] for h in hits), sorted({h[2].page for h in hits})


def query_parts(query: str, pages_with: Callable[[str], int]) -> list[str]:
    """The parts of a long query worth counting on their own, in query order (at most PARTS_MAX): quoted
    phrases, control and clause numbers, acronyms ("ASP", "N/S"), numbers with a unit word after them
    ("80 billion", "5 percent"),
    and the RARE_WORDS search words on the fewest pages (`pages_with(word)`; none counts too)."""
    q = " ".join(query.split())
    spans: list[tuple[int, int, str]] = []
    for rx, group in ((_QUOTED, 1), (_ID, 0), (_SECTION, 0), (_DOTTED, 0), (_ACRONYM, 0), (_NUMBER, 0)):
        for m in rx.finditer(q):
            a, b = m.span(group)
            if rx is _NUMBER and re.fullmatch(r"\d", m.group(0)):
                continue  # "Case Study 2": a bare digit says nothing
            if rx is _ACRONYM and m.group(0).lower() in _STOP:
                continue  # "BY", "OR" in capitals are stop words, not acronyms
            if m.group(group).strip() and not any(a < y and x < b for x, y, _ in spans):
                spans.append((a, b, m.group(group).strip()))
    taken = [(a, b) for a, b, _ in spans]
    words = []
    for m in re.finditer(r"[^\W\d_]{4,}", q):
        if not any(a <= m.start() < b for a, b in taken) and tokens(m.group(0)):
            words.append((pages_with(tokens(m.group(0))[0]), m.start(), m.group(0)))
    for _, at, w in sorted(words)[:RARE_WORDS]:
        spans.append((at, at + len(w), w))
    out: list[str] = []
    for _, _, text in sorted(spans):
        if text.lower() not in (x.lower() for x in out):
            out.append(text)
    return out[:PARTS_MAX]


def pages_with(name: str) -> Callable[[str], int]:
    """Pages of a document holding a search word (G's page index)."""
    from tglw.search.api import _models
    from tglw.search.tokens import doc_cjk_mode

    ix = load(name)
    body, _ = _models(ix, Config(cjk=doc_cjk_mode(ix.doc)))
    return lambda t: sum(1 for c in body.tf if t in c)


def parts_line(parts: list[dict[str, Any]]) -> str | None:
    return ("parts of the query (whole words): " + " | ".join(f'"{x["text"]}" {x["n"]}' for x in parts)) if parts else None


def variant_line(variants: list[dict[str, Any]], mode: str = "literal") -> str | None:
    """Literal: the other spellings' match counts; default search: passages scoring above 0 when retried
    with word stems or expanded abbreviations."""
    if mode == "bm25":
        parts = [f'{x["text"]}: {x["n"]} passages' + (f' · {_pages(x["pages"])}' if x["n"] else "") for x in variants]
        return ("retried with " + " | ".join(parts)) if parts else None
    found = [f'"{x["text"]}" {x["n"]} · {_pages(x["pages"])}' for x in variants if x["n"]]
    zero = [f'"{x["text"]}"' for x in variants if not x["n"]]
    parts = found + ([("0: " if found else "0 matches: ") + ", ".join(zero)] if zero else [])
    return ("other spellings: " + " | ".join(parts)) if parts else None


# ---- nearest passages ------------------------------------------------------------------------

class Line(NamedTuple):
    line: str
    addr: str | None
    page: int


Formatter = Callable[[Any, str, list[Hit]], list[Any]]  # items with .line, .addr, .page (views.api.hit_lines)


def plain_lines(v: Any, query: str, hits: list[Hit]) -> list[Line]:
    """A plain form of the search hit line, used until the reader's own formatter (views.api.hit_lines) is
    there."""
    from tglw.views.docview import short_ref

    out = []
    for i, h in enumerate(hits, 1):
        addr = short_ref(h.figure or h.refs[0]) if (h.figure or h.refs) else None
        path = " > ".join(h.section[-2:])
        out.append(Line(f"{i}. {addr or v.page_tag(h.page)} · {v.page_tag(h.page)}" + (f" · {path}" if path else "")
                        + f" | {' '.join(h.excerpt.split())}", addr, h.page))
    return out


def formatter() -> Formatter:
    from tglw.views import api

    return getattr(api, "hit_lines", None) or plain_lines


RETRIES = (("word stems", {"stem": "snowball"}), ("abbreviations expanded", {"expand": True}))


def retries(v: Any, query: str) -> list[dict[str, Any]]:
    """For a default search that found nothing: how many passages score above 0 with word stems, and with
    abbreviations expanded (task C1), and their pages."""
    from tglw.search.api import _unit_scores, _units
    from tglw.search.tokens import doc_cjk_mode

    ix, cjk = load(v.name), doc_cjk_mode(v.doc)
    out = []
    for how, opts in RETRIES:
        cfg = Config(cjk=cjk, **opts)
        scores = _unit_scores(ix, query, cfg)
        units = _units(ix, cfg)
        top = sorted((i for i, x in enumerate(scores) if x > 0), key=lambda i: -scores[i])
        out.append({"text": how, "n": len(top), "pages": sorted({b.pages[0] for i in top[:PAGES_SHOWN] for b in units[i][0][:1] if b.pages})})
    return out


def nearest(v: Any, query: str, mode: str, listed: set[str], fmt: Formatter) -> tuple[list[tuple[str, str | None, int, float]], str]:
    """Up to NEAR_MAX passages of G's block search for the query that this conversation has not listed;
    for a default search that found nothing, the same with word stems, then with abbreviations expanded.
    Returns (line, address, page, score) per passage, and which search found them."""
    from tglw.search.tokens import doc_cjk_mode

    cjk = doc_cjk_mode(v.doc)
    tries = [("", Config(cjk=cjk))] if mode == "literal" else []
    tries += [("word stems", Config(stem="snowball", cjk=cjk)), ("abbreviations expanded", Config(expand=True, cjk=cjk))]
    for how, cfg in tries:
        hits = [h for h in search(v.name, query, NEAR_MAX + len(listed), unit="block", config=cfg) if h.score > 0]
        if not hits:
            continue
        rows = [(x.line, x.addr, x.page, h.score) for x, h in zip(fmt(v, query, hits), hits) if x.addr not in listed]
        return rows[:NEAR_MAX], how
    return [], ""


def _renumber(lines: list[str]) -> list[str]:
    return [re.sub(r"^\d+\.", f"{i}.", line) for i, line in enumerate(lines, 1)]


def _shorten(line: str, words: int) -> str:
    head, sep, text = line.partition(" | ")
    w = text.split()
    return line if not sep or len(w) <= words else f"{head} | {' '.join(w[:words])} …"


# ---- the block -------------------------------------------------------------------------------

def absence(v: Any, query: str, mode: str, listed: Iterable[str] = (), budget: int = ABSENCE_MAX,
            fmt: Formatter | None = None) -> Absence | None:
    """The evidence block for a search of `v` (a views DocView, or a registered name) that found nothing.
    mode: "literal" or "bm25". `listed`: hit addresses this conversation has shown (not repeated).
    `budget`: tokens, capped at ABSENCE_MAX. None when the query, its 0 and the scope do not fit."""
    from tglw.search.tokens import doc_cjk_mode
    from tglw.views import api

    v = api.view(v) if isinstance(v, str) else v
    cap = min(budget, ABSENCE_MAX)
    sc = scope(v)
    head = scope_lines(query, mode, sc, doc_cjk_mode(v.doc).endswith(FOLD))
    long = mode == "literal" and len(query.split()) > PART_WORDS
    variants, parts = [], []
    for s in spellings(query, v.name) if mode == "literal" and not long else []:
        n, pages = count_literal(v, s)
        variants.append({"text": s, "n": n, "pages": pages})
    if mode == "bm25":
        variants += retries(v, query)
    if long:
        parts = [{"text": t, "n": count_literal(v, t, whole=True)[0]} for t in query_parts(query, pages_with(v.name))]
    variants.sort(key=lambda x: (x["n"] == 0,))
    with api._cjk_of(v):
        rows, how = nearest(v, query, mode, set(listed), fmt or formatter())
    near_lines = [r[0] for r in rows]
    trimmed = 0

    def assemble() -> list[str]:
        vl, pl = variant_line(variants, mode), parts_line(parts)
        title = "nearest passages" + (f" ({how})" if how else "") + ":"
        tail = [title] + _renumber(near_lines) if near_lines else ["no passage shares a word with the query"]
        return head + ([vl] if vl else []) + ([pl] if pl else []) + tail

    def size() -> int:
        return count_tokens("\n".join(assemble()))

    if size() > cap and len(near_lines) == NEAR_MAX:
        near_lines.pop()
        rows.pop()
        trimmed += 1
    while size() > cap and len([x for x in variants if not x["n"]]) and len(variants) > KEEP_ZERO_VARIANTS \
            and not variants[-1]["n"]:
        variants.pop()
        trimmed += 1
    for words in EXCERPT_WORDS:
        if size() > cap and near_lines:
            short = [_shorten(line, words) for line in near_lines]
            trimmed += sum(a != b for a, b in zip(near_lines, short))
            near_lines = short
    while size() > cap and len(parts) > PARTS_KEEP:
        parts.pop()
        trimmed += 1
    while size() > cap and near_lines:
        near_lines.pop()
        rows.pop()
        trimmed += 1
    while size() > cap and parts:
        parts.pop()
        trimmed += 1
    while size() > cap and variants:
        variants.pop()
        trimmed += 1
    if size() > cap:
        if count_tokens("\n".join(head)) > cap:
            return None
        lines = head
    else:
        lines = assemble()
    return Absence(lines, [{"addr": a, "page": p, "score": round(s, 3)} for _, a, p, s in rows],
                   variants, parts, sc, count_tokens("\n".join(lines)), trimmed)
