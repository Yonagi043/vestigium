"""`search(doc, query, k)`: ranked pages (or sections of blocks) with section path, printed page label,
and a short excerpt. Options (all off reproduces task E's S2 page ranking exactly):

- stem: "plural" strips plural endings; "snowball" is the Snowball English stemmer. Questions and text
  inflect the same word differently ("controls" / "control").
- expand: add C1 abbreviation <-> expansion pairs at half weight. Documents switch to the
  abbreviation after defining it once, questions use either form.
- titles: weight of a second BM25 field holding the titles of the sections on the page. Headings
  name what a passage is about in the words a question is likely to use.
- facets: rank with each clause of a multi-part question as well as with the whole question, and
  interleave the rankings. One ranking for the whole question favours the facet with the most
  distinctive words; interleaving puts every facet's best page near the top.
- overview: move the pages of overview sections (Abstract, Summary, Highlights, Executive Summary,
  Introduction, Overview; found from task A's titles, or a Docling heading on the page) that share a
  query term to right after the top hit. Such sections condense a document's main claims, which
  broad questions ask about. Derived from looking at the 6x10 failures: needs the H dev set to confirm.
- maxblock: score a page by its best-matching block instead of by the whole page (passage-level
  evidence, "MaxP"). A page mixing several topics dilutes a page-level score; the block that
  answers the question is denser in query terms.
- passages: score passages (a heading with what follows it, short blocks joined to about 60 words)
  instead of single blocks, for unit="block" and maxblock. A lone heading or one-line list item
  ("our dividend policy;") otherwise outranks the paragraph that discusses the topic. Each picture
  with inner text (caption, axis titles, labels) is one more passage; its hit is addressed by the
  picture (`refs == [figure]`, `figure` set), as task M5's literal search is; figures=False leaves
  them out (G v3's index).
- fuzzy: a query word of five or more letters that never occurs in the document gets the document's
  words within one edit of it, at half weight (at most three). Scanned pages misread characters
  ("uCLEAR" printed as "[tCLEAR", "(MCLEAR)"), so the exact word is missing while a near copy is there.
- diversify: pick pages one at a time; after each pick, halve the weight of the query terms that page
  contains. A multi-part question's later parts then outweigh the part already answered, without
  having to split the question into clauses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from tglw.common.bundle import Block, Doc

from .bm25 import BM25, SplitBM25
from .index import Index, load
from .tokens import CJK_MODES, FOLD, HAN, STEMS, cjk_mode, doc_cjk_mode, facets, squeeze_han, tokens
from .tokens import _fold as fold_text


@dataclass(frozen=True)
class Config:
    stem: str = "none"
    expand: bool = False
    titles: float = 0.0
    facets: bool = False
    diversify: bool = False
    overview: bool = False
    maxblock: bool = False
    passages: bool = True
    fuzzy: bool = False
    figures: bool = True
    cjk: str = field(default_factory=cjk_mode)

    def __post_init__(self) -> None:
        if self.stem not in STEMS or self.cjk not in CJK_MODES:
            raise ValueError((self.stem, self.cjk))


@dataclass
class Hit:
    page: int
    label: str | None
    section: list[str]
    score: float
    excerpt: str
    refs: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    heading: str | None = None
    fuzzy: bool = False
    figure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v or k in ("page", "score")}


FUZZY_MIN, FUZZY_MAX = 5, 3


def _near(ix: Index, t: str, cfg: Config) -> list[str]:
    """Document words one edit away from a word the document lacks (most frequent first)."""
    from rapidfuzz.distance import Levenshtein

    body, _ = _models(ix, Config(stem=cfg.stem, cjk=cfg.cjk))
    if t in body.idf or len(t) < FUZZY_MIN or not t.isalpha():
        return []
    df = ix.cache.setdefault(("df", cfg.stem, cfg.cjk), {v: sum(1 for c in body.tf if v in c) for v in body.idf})
    near = [v for v in df if abs(len(v) - len(t)) <= 1 and len(v) >= FUZZY_MIN - 1
            and Levenshtein.distance(v, t, score_cutoff=1) <= 1]
    return sorted(near, key=lambda v: (-df[v], v))[:FUZZY_MAX]


def _weights(ix: Index, query: str, cfg: Config) -> dict[str, float]:
    w: dict[str, float] = {}
    for t in tokens(query, cfg.stem, cfg.cjk):
        w[t] = w.get(t, 0.0) + 1.0
    if cfg.fuzzy:
        for t in list(w):
            for v in _near(ix, t, cfg):
                w.setdefault(v, 0.5)
    if cfg.expand:
        low = query.lower()
        for key, alts in ix.expand.items():
            hit = re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", query if key != key.lower() else low)
            if not hit:
                continue
            for alt in alts:
                for t in tokens(alt, cfg.stem, cfg.cjk):
                    if t not in w:
                        w[t] = 0.5
    return w


def _is_han(t: str) -> bool:
    return bool(_HAN_TOKEN.match(t))


def _bm25(corpus: list[list[str]], cfg: Config) -> BM25 | SplitBM25:
    """One BM25, or with a CJK mode two summed fields (Han tokens and the rest)."""
    return BM25(corpus) if cfg.cjk == "off" else SplitBM25(corpus, _is_han)


def _models(ix: Index, cfg: Config) -> tuple[BM25 | SplitBM25, BM25 | SplitBM25 | None]:
    key = ("pages", cfg.stem, cfg.cjk)
    if key not in ix.cache:
        body = _bm25([tokens(ix.page_text[p], cfg.stem, cfg.cjk) for p in ix.pages], cfg)
        titles = _bm25([tokens(ix.titles(p), cfg.stem, cfg.cjk) for p in ix.pages], cfg)
        ix.cache[key] = (body, titles)
    body, titles = ix.cache[key]
    return body, titles if cfg.titles else None


Unit = tuple[list[Block], str | None, str | None, str | None]


def _units(ix: Index, cfg: Config) -> list[Unit]:
    """(blocks, section node, heading, picture ref) per scored unit: passages (pictures included), or
    single text blocks."""
    if cfg.passages:
        return [(p.blocks, p.node, p.heading, p.figure) for p in ix.passages() if cfg.figures or not p.figure]
    return [([b], ix.block_node(b), None, None) for b in ix.blocks]


def _unit_scores(ix: Index, query: str, cfg: Config) -> list[float]:
    """BM25 per unit. Text passages are scored with statistics from text passages alone, so adding
    pictures leaves their order exactly as before; pictures are scored with statistics over all units,
    and each word counts once per picture (charts repeat tick and legend labels, as in task M5's
    literal search a repeated label does not outrank prose)."""
    key = ("units", cfg.stem, bool(cfg.titles), cfg.passages, cfg.figures, cfg.cjk)
    if key not in ix.cache:
        docs, is_fig = [], []
        for blocks, node, _, figure in _units(ix, cfg):
            text = " ".join(b.text for b in blocks)
            toks = tokens(text + " " + " ".join(ix.sections.path(node)) if cfg.titles else text, cfg.stem, cfg.cjk)
            docs.append(list(dict.fromkeys(toks)) if figure else toks)
            is_fig.append(bool(figure))
        text_model = _bm25([d for d, f in zip(docs, is_fig) if not f], cfg)
        fig_model = _bm25(docs, cfg) if any(is_fig) else None
        ix.cache[key] = (text_model, fig_model, is_fig)
    text_model, fig_model, is_fig = ix.cache[key]
    w = _weights(ix, query, cfg)
    text_scores = iter(text_model.scores(w))
    fig_scores = fig_model.scores(w) if fig_model else []
    return [fig_scores[i] if f else next(text_scores) for i, f in enumerate(is_fig)]


def _rank_maxblock(ix: Index, query: str, cfg: Config) -> list[tuple[int, float]]:
    best: dict[int, float] = dict.fromkeys(ix.pages, 0.0)
    for (blocks, _, _, _), sc in zip(_units(ix, cfg), _unit_scores(ix, query, cfg)):
        for p in {p for b in blocks for p in b.pages}:
            if p in best and sc > best[p]:
                best[p] = sc
    return sorted(best.items(), key=lambda x: (-x[1], x[0]))


def _rank(ix: Index, query: str, cfg: Config) -> list[tuple[int, float]]:
    if cfg.maxblock:
        return _rank_maxblock(ix, query, cfg)
    body, titles = _models(ix, cfg)
    w = _weights(ix, query, cfg)
    if not w:
        return [(p, 0.0) for p in ix.pages]
    s = body.scores(w if cfg.expand or cfg.fuzzy else tokens(query, cfg.stem, cfg.cjk))
    if titles is not None:
        s = [a + cfg.titles * b for a, b in zip(s, titles.scores(w))]
    pages = ix.pages
    return sorted(((pages[i], s[i]) for i in range(len(pages))), key=lambda x: (-x[1], x[0]))


OVERVIEW = re.compile(r"^(?:[\dIVX]+\.?\s+)?(?:abstract|summary|executive summary|highlights|overview|introduction)\b",
                      re.I)
MAX_OVERVIEW = 2


def overview_pages(ix: Index) -> list[int]:
    """Pages of overview sections, in page order."""
    key = "overview"
    if key not in ix.cache:
        pages = []
        heads = {b.page for b in ix.doc.headings() if b.layer == "body" and OVERVIEW.match(b.text.strip())}
        for p in ix.pages:
            chain = [ix.sections.title[n] for node in ix.page_nodes.get(p, {}) for n in ix.sections.chain(node)]
            if p in heads or any(OVERVIEW.match(t) for t in chain):
                pages.append(p)
        ix.cache[key] = pages
    return ix.cache[key]


def _with_overview(ix: Index, query: str, cfg: Config, ranked: list[tuple[int, float]]) -> list[tuple[int, float]]:
    q = set(tokens(query, cfg.stem, cfg.cjk))
    body, _ = _models(ix, cfg)
    pos = {p: i for i, p in enumerate(ix.pages)}
    picks = [p for p in overview_pages(ix) if q & set(body.tf[pos[p]])][:MAX_OVERVIEW]
    if not ranked or not picks:
        return ranked
    head = ranked[:1] + [x for x in ranked if x[0] in picks and x[0] != ranked[0][0]]
    return head + [x for x in ranked[1:] if x[0] not in picks]


DIVERSIFY_DEPTH = 30
DECAY = 0.5


def _diversified(ix: Index, query: str, cfg: Config, first: list[tuple[int, float]]) -> list[tuple[int, float]]:
    body, _ = _models(ix, cfg)
    w = _weights(ix, query, cfg)
    pos = {p: i for i, p in enumerate(ix.pages)}
    pool = [p for p, _ in first[:DIVERSIFY_DEPTH]]
    out: list[tuple[int, float]] = []
    while pool:
        s = body.scores(w)
        best = max(pool, key=lambda p: (s[pos[p]], -pool.index(p)))
        out.append((best, s[pos[best]]))
        pool.remove(best)
        for t in body.tf[pos[best]]:
            if t in w:
                w[t] *= DECAY
    return out + first[DIVERSIFY_DEPTH:]


def rank_pages(ix: Index, query: str, cfg: Config = Config()) -> list[tuple[int, float]]:
    """Every page, best first."""
    first = _rank(ix, query, cfg)
    if cfg.overview:
        first = _with_overview(ix, query, cfg, first)
    if cfg.diversify:
        return _diversified(ix, query, cfg, first)
    parts = facets(query) if cfg.facets else []
    if len(parts) < 2:
        return first
    lists = [first] + [_rank(ix, q, cfg) for q in parts]
    score = dict(first)
    out: list[tuple[int, float]] = []
    seen: set[int] = set()
    for row in zip(*lists):
        for p, _ in row:
            if p not in seen:
                seen.add(p)
                out.append((p, score[p]))
    return out


_EXCERPT_UNIT = re.compile(rf"[{HAN}]|[^\s{HAN}]+")
_HAN_TOKEN = re.compile(rf"[{HAN}]")


def _join(units: list[str]) -> str:
    """Units back to text: no space between two Han characters."""
    out = ""
    for u in units:
        out += u if out and re.match(rf"[{HAN}]", u) and re.match(rf"[{HAN}]", out[-1]) else (" " if out else "") + u
    return out


def excerpt(text: str, query_terms: set[str], stem: str, width: int = 40, cjk: str = "off") -> str:
    """The window of `width` words (with a CJK mode: words and single Han characters) holding most
    query terms."""
    if cjk == "off" or not _HAN_TOKEN.search(text):
        words = text.split()
        if len(words) <= width:
            return text
        marks = [1 if set(tokens(w, stem, cjk)) & query_terms else 0 for w in words]
        join = " ".join
    else:
        words = _EXCERPT_UNIT.findall(squeeze_han(text))
        if len(words) <= width:
            return _join(words)
        keys = [fold_text(w) for w in words] if cjk.endswith(FOLD) else words
        pairs = {keys[i] + keys[i + 1] for i in range(len(keys) - 1)}
        marks = []
        for i, w in enumerate(words):
            k = keys[i]
            near = (keys[i - 1] + k if i else "", k + keys[i + 1] if i + 1 < len(keys) else "")
            marks.append(1 if set(tokens(w, stem, cjk)) & query_terms or (set(near) & pairs & query_terms) else 0)
        join = _join
    best = max(range(0, len(words) - width + 1), key=lambda i: (sum(marks[i:i + width]), -i))
    return ("… " if best else "") + join(words[best:best + width]) + (" …" if best + width < len(words) else "")


def _block_hits(ix: Index, query: str, cfg: Config, k: int) -> list[Hit]:
    w = _weights(ix, query, cfg)
    units = _units(ix, cfg)
    s = _unit_scores(ix, query, cfg)
    hits: dict[str, Hit] = {}  # one hit per (section, heading): passages under one subheading merge
    for i in sorted(range(len(units)), key=lambda i: (-s[i], i)):
        if s[i] <= 0:
            break
        blocks, node, heading, figure = units[i]
        key = figure or f"{node or blocks[0].ref}|{heading}"
        if key not in hits and len(hits) >= k:
            continue
        h = hits.get(key)
        if h is None:
            text = " ".join(b.text for b in blocks)
            refs = [figure] if figure else [b.ref for b in blocks][:5]
            hits[key] = Hit(blocks[0].page, ix.labels.get(blocks[0].page), ix.sections.path(node), round(s[i], 3),
                            excerpt(text, set(w), cfg.stem, cjk=cfg.cjk), refs, sorted({p for b in blocks for p in b.pages}),
                            heading, figure=figure)
        elif len(h.refs) < 5:
            h.refs += [b.ref for b in blocks][: 5 - len(h.refs)]
            h.pages = sorted(set(h.pages) | {p for b in blocks for p in b.pages})
        if len(hits) >= k and all(len(x.refs) >= 5 for x in hits.values()):
            break
    return list(hits.values())[:k]


def _page_heading(ix: Index, page: int, query: str, cfg: Config) -> tuple[str | None, str | None]:
    """The heading and text of the best-scoring passage on a page, for a page hit's context."""
    units = _units(ix, cfg)
    s = _unit_scores(ix, query, cfg)
    best = max((i for i, (blocks, _, _, _) in enumerate(units) if any(page in b.pages for b in blocks)),
               key=lambda i: s[i], default=None)
    if best is None or s[best] <= 0:
        return None, None
    blocks, _, heading, _ = units[best]
    return heading, " ".join(b.text for b in blocks if page in b.pages)


def search(doc: str | Doc, query: str, k: int = 5, unit: str = "page", config: Config | None = None) -> list[Hit]:
    """Top-k pages (unit="page") or sections of matching blocks (unit="block", hits in one section merged).

    `doc` is a registry name such as "nist__NIST.SP.800-53r5", or a loaded `bundle.Doc` (its `.name` is used).
    The default is plain BM25: on the 59 6x10 evaluation questions no option beat it overall.
    When nothing matches at all (every score 0), the search is repeated with `fuzzy=True` and the hits
    say `fuzzy=True`: an OCR-damaged word ("uCLEAR" printed as "[tCLEAR") then still finds its page.
    With a CJK mode on (TGLW_CJK, read at each call), a query without one searchable word returns []
    rather than a list of pages scored 0. Without a config, the mode is chosen per document: unless
    TGLW_SEARCH_CJK_AUTO is off, a Chinese document gets CJK search while TGLW_CJK is unset, and unless
    TGLW_SEARCH_FOLD is off, traditional and simplified characters are folded (see tokens.py).
    """
    ix = load(doc if isinstance(doc, str) else doc.name)
    config = config or Config(cjk=doc_cjk_mode(ix.doc))
    if config.cjk != "off" and not tokens(query, config.stem, config.cjk):
        return []
    hits = _search(ix, query, k, unit, config)
    if not config.fuzzy and not any(h.score > 0 for h in hits):
        retry = _search(ix, query, k, unit, replace(config, fuzzy=True))
        if any(h.score > 0 for h in retry):
            for h in retry:
                h.fuzzy = True
            return retry
    return hits


def _search(ix: Index, query: str, k: int, unit: str, config: Config) -> list[Hit]:
    if unit == "block":
        return _block_hits(ix, query, config, k)
    q = set(_weights(ix, query, config))
    hits = []
    for p, sc in rank_pages(ix, query, config)[:k]:
        heading, text = _page_heading(ix, p, query, config)
        hits.append(Hit(p, ix.labels.get(p), ix.sections.path(ix.page_node(p)), round(sc, 3),
                        excerpt(text or ix.page_text[p], q, config.stem, cjk=config.cjk), heading=heading))
    return hits
