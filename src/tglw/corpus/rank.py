"""Library search: G's BM25 over the passages of every indexed document, hits tagged with their document.

Scores are G's (tglw.search.bm25: k1 1.5, b 0.75, negative idf floored at 0.25 x the mean idf; Han
terms a second field with its own lengths), with statistics over the whole library: text passages
are scored with text-passage statistics, pictures with statistics over all units, as G does within
one document. Hits merge as G's block hits do (one per document, section and heading; a picture is
one hit), so a hit names a document, a block address, its page and section, and an excerpt.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tglw.search.api import excerpt as g_excerpt
from tglw.search.tokens import HAN, tokens
from . import store

K1, B = 1.5, 0.75
FUZZY_MIN, FUZZY_MAX, FUZZY_WEIGHT = 5, 3, 0.5
REFS_PER_HIT = 5
_HAN_CHAR = re.compile(f"[{HAN}]")
_SHORT = {"texts": "t", "tables": "tab", "pictures": "fig"}


def short_ref(ref: str) -> str:
    """'#/texts/123' -> 't123' (the reader's block addresses)."""
    _, kind, idx = ref.split("/")
    return f"{_SHORT[kind]}{idx}"


@dataclass
class Hit:
    doc: str
    addr: str  # short address of the block holding the excerpt (or the picture): t123, tab4, fig5
    page: int
    label: str | None  # printed page label (task B)
    sid: str | None  # task A section
    section: list[str]
    heading: str | None
    score: float
    excerpt: str
    refs: list[str] = field(default_factory=list)  # the passage's blocks (short addresses), at most REFS_PER_HIT
    pages: list[int] = field(default_factory=list)
    figure: bool = False
    fuzzy: bool = False
    same: list[str] = field(default_factory=list)  # addresses of the same passage text in other documents

    @property
    def address(self) -> str:
        return f"{self.doc}:{self.addr}"

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "address": self.address}


@dataclass
class Library:
    """One open index with its per-unit arrays, reloaded when the index's generation changes. Each thread
    queries through its own connection (a server runs tool calls on worker threads)."""

    path: Path
    first: sqlite3.Connection
    generation: str
    stats: dict[str, Any]
    doc: dict[int, str]
    figure: dict[int, str | None]
    node: dict[int, str | None]
    heading: dict[int, str | None]
    length: dict[int, tuple[int, int]]
    place: dict[int, tuple[str, int]]  # (document, passage order): ties rank the same however the index was built
    docs: list[str]
    local: threading.local = field(default_factory=threading.local)

    @property
    def con(self) -> sqlite3.Connection:
        con = getattr(self.local, "con", None)
        if con is None:
            con = self.local.con = store.connect(self.path)
        return con

    @classmethod
    def open(cls, path: Path) -> Library:
        con = store.connect(path)
        meta = dict(con.execute("SELECT key, value FROM meta"))
        want = store.index_meta()
        if any(meta.get(k) != v for k, v in want.items()):
            raise RuntimeError(f"library index {path} was built with {({k: meta.get(k) for k in want})}, this code "
                               f"needs {want}: rebuild it (python -m tglw.corpus build)")
        rows = con.execute("SELECT id, doc, figure, node, heading, len_w, len_h, ord FROM units").fetchall()
        return cls(path, con, meta.get("generation", "0"), json.loads(meta.get("stats", "{}")),
                   {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}, {r[0]: r[3] for r in rows},
                   {r[0]: r[4] for r in rows}, {r[0]: (r[5], r[6]) for r in rows}, {r[0]: (r[1], r[7]) for r in rows},
                   [r[0] for r in con.execute("SELECT name FROM docs ORDER BY name")])

    def __post_init__(self) -> None:
        self.local.con = self.first  # the opening thread keeps the connection that read the arrays

    def current(self) -> bool:
        row = self.con.execute("SELECT value FROM meta WHERE key = 'generation'").fetchone()
        return (row[0] if row else "0") == self.generation

    def df(self, term: str) -> tuple[int, int]:
        """(text units, picture units) containing the term."""
        a = self.con.execute("SELECT doc FROM rtext WHERE term = ?", (term,)).fetchone()
        b = self.con.execute("SELECT doc FROM rfig WHERE term = ?", (term,)).fetchone()
        return (a[0] if a else 0), (b[0] if b else 0)

    def postings(self, table: str, term: str) -> list[tuple[int, int]]:
        return self.con.execute(f"SELECT doc, COUNT(*) FROM {table} WHERE term = ? GROUP BY doc", (term,)).fetchall()

    def vocabulary(self) -> Counter:
        """Every term with its number of units (for near spellings)."""
        out = Counter(dict(self.con.execute("SELECT term, doc FROM rtext")))
        out.update(dict(self.con.execute("SELECT term, doc FROM rfig")))
        return out


_OPEN: dict[Path, Library] = {}
_LOCK = threading.Lock()


def library(path: Path | None = None) -> Library:
    path = (path or store.db_path()).resolve()
    with _LOCK:
        lib = _OPEN.get(path)
        if lib is None or not lib.current():
            lib = _OPEN[path] = Library.open(path)
        return lib


def _idf(n: int, df: int, mean: float) -> float:
    raw = math.log(n - df + 0.5) - math.log(df + 0.5)
    return raw if raw >= 0 else store.EPSILON * mean


def _model_scores(lib: Library, weights: dict[str, float], model: str) -> dict[int, float]:
    """G's BM25 sum per unit for one model: text units from ftext, picture units from ffig. The
    non-Han and Han fields are summed separately and then added, as SplitBM25 does."""
    table = "vtext" if model == "text" else "vfig"
    parts: list[dict[int, float]] = []
    for fld in store.FIELDS:
        st = lib.stats.get(f"{model}.{fld}", {})
        n, total = st.get("n", 0), st.get("total", 0.0)
        out: dict[int, float] = {}
        parts.append(out)
        if not n or not total:
            continue
        avgdl = total / n
        for t, w in weights.items():
            if store.is_han(t) != (fld == "h"):
                continue
            dt, dfig = lib.df(t)
            df = dt if model == "text" else dt + dfig
            if not df:
                continue
            idf = _idf(n, df, st["mean_idf"])
            if not idf:
                continue
            for uid, f in lib.postings(table, t):
                norm = K1 * (1 - B + B * lib.length[uid][fld == "h"] / avgdl)
                out[uid] = out.get(uid, 0.0) + w * idf * f * (K1 + 1) / (f + norm)
    w_part, h_part = parts
    return {u: w_part.get(u, 0.0) + h_part.get(u, 0.0) for u in w_part.keys() | h_part.keys()}


def unit_scores(lib: Library, weights: dict[str, float]) -> dict[int, float]:
    """Positive scores of every unit sharing a query term."""
    scores = _model_scores(lib, weights, "text")
    scores.update(_model_scores(lib, weights, "all"))
    return {u: s for u, s in scores.items() if s > 0}


def query_mode(query: str) -> str:
    """The index's folded Han pairs for a query with Han characters, else G's default tokens (an English
    query scores exactly as G's with TGLW_CJK off)."""
    return store.CJK if _HAN_CHAR.search(query) else "off"


def weights(query: str, mode: str) -> dict[str, float]:
    w: dict[str, float] = {}
    for t in tokens(query, "none", mode):
        w[t] = w.get(t, 0.0) + 1.0
    return w


def near_words(lib: Library, w: dict[str, float]) -> dict[str, float]:
    """G's fuzzy retry over the library vocabulary: a query word of FUZZY_MIN+ letters found nowhere
    gets up to FUZZY_MAX words one edit away (most frequent first) at half weight."""
    from rapidfuzz.distance import Levenshtein

    vocab = lib.vocabulary()
    out = dict(w)
    for t in w:
        if t in vocab or len(t) < FUZZY_MIN or not t.isalpha():
            continue
        near = [v for v in vocab if abs(len(v) - len(t)) <= 1 and len(v) >= FUZZY_MIN - 1
                and Levenshtein.distance(v, t, score_cutoff=1) <= 1]
        for v in sorted(near, key=lambda v: (-vocab[v], v))[:FUZZY_MAX]:
            out.setdefault(v, FUZZY_WEIGHT)
    return out


def _owner(blocks: list[list[Any]], terms: set[str], mode: str) -> int:
    """The block of a passage with the most distinct query terms, then the most query terms; first wins."""
    def key(i: int) -> tuple[int, int, int]:
        toks = tokens(blocks[i][2], "none", mode)
        return len(terms.intersection(toks)), sum(t in terms for t in toks), -i
    return max(range(len(blocks)), key=key)


def _path(lib: Library, doc: str, sid: str | None) -> list[str]:
    out = []
    while sid:
        row = lib.con.execute("SELECT parent, label, root FROM sections WHERE doc = ? AND sid = ?", (doc, sid)).fetchone()
        if row is None or row[2]:
            break
        out.append(row[1])
        sid = row[0]
    return out[::-1]


def _label(lib: Library, doc: str, page: int) -> str | None:
    row = lib.con.execute("SELECT label FROM labels WHERE doc = ? AND page = ?", (doc, page)).fetchone()
    return row[0] if row else None


@dataclass
class Result:
    hits: list[Hit]
    docs: list[tuple[str, float]]  # documents by their best unit, best first
    words: bool  # whether the query had a searchable word


def query(query: str, k: int = 5, docs: list[str] | None = None, unit: str = "block",
          path: Path | None = None) -> Result:
    """Top-k hits over the library (or over `docs`), best first, and the documents ranked by their best
    passage. unit "block": one hit per document, section and heading, as G's block search; "page": one
    hit per document and page. When nothing matches, near spellings are tried (hits marked fuzzy)."""
    if unit not in ("block", "page"):
        raise ValueError(f"unit must be 'block' or 'page', not {unit!r}")
    lib = library(path)
    mode = query_mode(query)
    w = weights(query, mode)
    if not w:
        return Result([], [], False)
    allow = set(docs) if docs is not None else None
    scores = _restrict(lib, unit_scores(lib, w), allow)
    fuzzy = False
    if not scores:
        w = near_words(lib, w)
        scores = _restrict(lib, unit_scores(lib, w), allow)
        fuzzy = bool(scores)
    return Result(_hits(lib, scores, w, mode, k, unit, fuzzy), doc_ranking(lib, scores), True)


def search(query_text: str, k: int = 5, docs: list[str] | None = None, unit: str = "block",
           path: Path | None = None) -> list[Hit]:
    """The hits of `query` (a query with no searchable word gives [])."""
    return query(query_text, k, docs, unit, path).hits


def _restrict(lib: Library, scores: dict[int, float], allow: set[str] | None) -> dict[int, float]:
    return scores if allow is None else {u: s for u, s in scores.items() if lib.doc[u] in allow}


def doc_ranking(lib: Library, scores: dict[int, float]) -> list[tuple[str, float]]:
    """Documents by their best unit (the routing measure of RS-IR's crossdoc)."""
    best: dict[str, float] = {}
    for u, s in scores.items():
        d = lib.doc[u]
        if s > best.get(d, 0.0):
            best[d] = s
    return sorted(best.items(), key=lambda x: (-x[1], x[0]))


def _hits(lib: Library, scores: dict[int, float], w: dict[str, float], mode: str, k: int, unit: str,
          fuzzy: bool) -> list[Hit]:
    """G's block-hit merging over the library. A passage whose text a hit from another document already
    shows (a re-issued or amended copy) adds its address to that hit instead of taking a place."""
    hits: dict[tuple[str, str], Hit] = {}
    shown: dict[str, Hit] = {}  # passage text -> the hit showing it
    terms = set(w)
    for uid in sorted(scores, key=lambda u: (-scores[u], lib.place[u])):
        doc, figure = lib.doc[uid], lib.figure[uid]
        blocks = None
        if unit == "page":
            blocks = _blocks(lib, uid)
            key = (doc, str(blocks[_owner(blocks, terms, mode)][1][0]))
        else:
            key = (doc, figure or f"{lib.node[uid] or uid}|{lib.heading[uid]}")
        if key not in hits and len(hits) >= k:
            continue
        blocks = blocks or _blocks(lib, uid)
        h = hits.get(key)
        if h is None:
            text = " ".join(b[2] for b in blocks)
            twin = shown.get(text)
            if twin is not None and twin.doc != doc:
                addr = short_ref(figure) if figure else short_ref(blocks[_owner(blocks, terms, mode)][0])
                if all(not a.startswith(f"{doc}:") for a in twin.same):
                    twin.same.append(f"{doc}:{addr}")
                continue
            hits[key] = shown[text] = _hit(lib, uid, blocks, scores[uid], terms, mode, fuzzy)
        elif len(h.refs) < REFS_PER_HIT:
            h.refs += [short_ref(b[0]) for b in blocks][: REFS_PER_HIT - len(h.refs)]
            h.pages = sorted(set(h.pages) | {p for b in blocks for p in b[1]})
        if len(hits) >= k and all(len(x.refs) >= REFS_PER_HIT for x in hits.values()):
            break
    return list(hits.values())[:k]


def _blocks(lib: Library, uid: int) -> list[list[Any]]:
    return json.loads(lib.con.execute("SELECT blocks FROM units WHERE id = ?", (uid,)).fetchone()[0])


def _hit(lib: Library, uid: int, blocks: list[list[Any]], score: float, terms: set[str], mode: str,
         fuzzy: bool) -> Hit:
    doc, figure = lib.doc[uid], lib.figure[uid]
    text = " ".join(b[2] for b in blocks)
    own = blocks[_owner(blocks, terms, mode)]
    page = own[1][0] if own[1] else blocks[0][1][0]
    addr = short_ref(figure) if figure else short_ref(own[0])
    refs = [addr] if figure else [short_ref(b[0]) for b in blocks][:REFS_PER_HIT]
    sid = lib.node[uid]
    return Hit(doc, addr, page, _label(lib, doc, page), sid, _path(lib, doc, sid), lib.heading[uid], round(score, 3),
               " ".join(g_excerpt(text, terms, "none", cjk=mode).split()), refs, sorted({p for b in blocks for p in b[1]}),
               figure=bool(figure), fuzzy=fuzzy)


def index_info(path: Path | None = None) -> dict[str, Any]:
    lib = library(path)
    return {"path": str(lib.path), "documents": len(lib.docs), "units": len(lib.doc), "generation": lib.generation,
            "bytes": os.path.getsize(lib.path)}
