"""The library index: G's passages of every document in one SQLite file, terms in two FTS5 tables.

Units are G's passages (tglw.search.index.Index.passages): text passages in `ftext`, picture passages
(caption and inner text, each word once) in `ffig`. Terms are G's tokens with folded Han pairs
("bi+fold"), so the non-Han terms of a unit are exactly G's terms with TGLW_CJK off. FTS5 only stores the postings; scores
are G's BM25 (tglw.corpus.rank) from per-term postings and the statistics kept in `meta`.

One document is replaced at a time (`update`), so a new PDF is added without rebuilding the rest.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tglw.common import bundle, registry
from tglw.common.paths import DATA
from tglw.search import index as g_index
from tglw.search.tokens import HAN, tokens

SCHEMA = 1
DB_ENV = "TGLW_CORPUS_DB"
CJK = "bi+fold"  # Han pairs, traditional and simplified folded together (tglw.zh.fold), are always indexed
_HAN_TERM = re.compile(f"[{HAN}]")
FIELDS = ("w", "h")  # non-Han terms, Han terms: two BM25 fields as in G's SplitBM25
MODELS = ("text", "all")  # text passages alone; text and picture passages (G scores pictures with both)
EPSILON = 0.25

_DDL = f"""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs(name TEXT PRIMARY KEY, sources TEXT NOT NULL, units INTEGER NOT NULL,
                                pages INTEGER NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY, doc TEXT NOT NULL, ord INTEGER NOT NULL,
                                 figure TEXT, node TEXT, heading TEXT, blocks TEXT NOT NULL,
                                 len_w INTEGER NOT NULL, len_h INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS units_doc ON units(doc);
CREATE TABLE IF NOT EXISTS sections(doc TEXT NOT NULL, sid TEXT NOT NULL, parent TEXT, label TEXT NOT NULL,
                                    root INTEGER NOT NULL, PRIMARY KEY(doc, sid));
CREATE TABLE IF NOT EXISTS labels(doc TEXT NOT NULL, page INTEGER NOT NULL, label TEXT NOT NULL,
                                  PRIMARY KEY(doc, page));
CREATE VIRTUAL TABLE IF NOT EXISTS ftext USING fts5(toks, tokenize='unicode61 remove_diacritics 0');
CREATE VIRTUAL TABLE IF NOT EXISTS ffig USING fts5(toks, tokenize='unicode61 remove_diacritics 0');
CREATE VIRTUAL TABLE IF NOT EXISTS vtext USING fts5vocab(ftext, 'instance');
CREATE VIRTUAL TABLE IF NOT EXISTS vfig USING fts5vocab(ffig, 'instance');
CREATE VIRTUAL TABLE IF NOT EXISTS rtext USING fts5vocab(ftext, 'row');
CREATE VIRTUAL TABLE IF NOT EXISTS rfig USING fts5vocab(ffig, 'row');
"""


def db_path() -> Path:
    raw = os.environ.get(DB_ENV)
    return Path(raw).expanduser() if raw else DATA / "corpus" / "corpus.sqlite"


def connect(path: Path | None = None, create: bool = False) -> sqlite3.Connection:
    path = path or db_path()
    if not create and not path.exists():
        raise FileNotFoundError(f"no library index at {path}; build it with: python -m tglw.corpus build")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)  # each thread has its own; closing one from another is safe
    con.execute("PRAGMA journal_mode=WAL")
    if create:
        con.executescript(_DDL)
        for key, value in index_meta().items():
            con.execute("INSERT OR IGNORE INTO meta VALUES(?, ?)", (key, value))
        con.commit()
    return con


def index_meta() -> dict[str, str]:
    """What the units and terms depend on besides the documents and their sidecars; an index built
    otherwise must be rebuilt."""
    from tglw.zh import FOLD_AVAILABLE

    return {"schema": str(SCHEMA), "cjk": CJK, "fold_tables": "opencc" if FOLD_AVAILABLE else "nfkc-only",
            "running_headers": "split" if _on(g_index.RUNNING_HEADERS_ENV) else "off"}


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "on", "yes")


def mismatch(con: sqlite3.Connection) -> dict[str, tuple[str | None, str]]:
    """Settings the index was built with that differ from this process's: key -> (index, here)."""
    have = dict(con.execute("SELECT key, value FROM meta"))
    return {k: (have.get(k), v) for k, v in index_meta().items() if have.get(k) != v}


def _reset(con: sqlite3.Connection) -> None:
    for table in ("units", "sections", "labels", "docs", "ftext", "ffig"):
        con.execute(f"DELETE FROM {table}")
    for key, value in index_meta().items():
        con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, value))


def is_han(term: str) -> bool:
    return bool(_HAN_TERM.match(term))


@dataclass
class Unit:
    figure: str | None
    node: str | None
    heading: str | None
    blocks: list[tuple[str, list[int], str]]  # (Docling ref, pages, text)
    toks: list[str]

    @property
    def lengths(self) -> tuple[int, int]:
        han = sum(map(is_han, self.toks))
        return len(self.toks) - han, han


def units(ix: g_index.Index) -> list[Unit]:
    """G's passages as scored by G (tglw.search.api._unit_scores, titles off): a picture's words once."""
    out = []
    for p in ix.passages():
        toks = tokens(" ".join(b.text for b in p.blocks), "none", CJK)
        if p.figure:
            toks = list(dict.fromkeys(toks))
        out.append(Unit(p.figure, p.node, p.heading, [(b.ref, list(b.pages), b.text) for b in p.blocks], toks))
    return out


def _digest(path: Path | None) -> str | None:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16] if path and path.exists() else None


def _sidecar_path(task: str, suffix: str, name: str) -> Path | None:
    root = g_index.delivered(task, suffix)
    return root / f"{name}{suffix}" if root else None


def sources(name: str) -> dict[str, Any]:
    """What a document's rows are built from: its bundle and the A (sections) and B (page labels) sidecars."""
    return {"bundle": registry.get(name).sha256,
            **{t: _digest(_sidecar_path(t, s, name)) for t, s in (("A", ".structure.json"), ("B", ".pagemap.json"))}}


def _section_rows(name: str) -> list[tuple[str, str, str | None, str, int]]:
    sc = g_index._sidecar("A", ".structure.json", name) or {}
    rows = []
    for n in sc.get("nodes") or []:
        title = " ".join((n.get("title") or "").split())
        num = " ".join((n.get("number") or "").split())
        label = title if not num or title.startswith(num) else f"{num} {title}"
        rows.append((name, n["id"], n.get("parent"), label, int(n.get("kind") == "root")))
    return rows


def remove(con: sqlite3.Connection, name: str) -> int:
    """Delete one document's rows; returns how many units it had. Statistics are not refreshed."""
    ids = [r[0] for r in con.execute("SELECT id FROM units WHERE doc = ?", (name,))]
    for table in ("ftext", "ffig"):
        con.executemany(f"DELETE FROM {table} WHERE rowid = ?", ((i,) for i in ids))
    for table in ("units", "sections", "labels"):
        con.execute(f"DELETE FROM {table} WHERE doc = ?", (name,))
    con.execute("DELETE FROM docs WHERE name = ?", (name,))
    return len(ids)


def add(con: sqlite3.Connection, name: str) -> int:
    """Index one document (its rows must not exist); returns its number of units."""
    doc = bundle.load(name)
    ix = g_index.build(doc)
    rows = units(ix)
    start = (con.execute("SELECT MAX(id) FROM units").fetchone()[0] or 0) + 1
    for i, u in enumerate(rows):
        uid = start + i
        w, h = u.lengths
        con.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?)",
                    (uid, name, i, u.figure, u.node, u.heading, json.dumps(u.blocks, ensure_ascii=False), w, h))
        con.execute(f"INSERT INTO {'ffig' if u.figure else 'ftext'}(rowid, toks) VALUES (?, ?)", (uid, " ".join(u.toks)))
    con.executemany("INSERT INTO sections VALUES (?,?,?,?,?)", _section_rows(name))
    con.executemany("INSERT INTO labels VALUES (?,?,?)", [(name, p, lab) for p, lab in sorted(ix.labels.items())])
    con.execute("INSERT INTO docs VALUES (?,?,?,?,?)", (name, json.dumps(sources(name)), len(rows), doc.page_count,
                                                       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    return len(rows)


def _df(con: sqlite3.Connection, table: str) -> Counter:
    return Counter(dict(con.execute(f"SELECT term, doc FROM {table}")))


def refresh_stats(con: sqlite3.Connection) -> dict[str, Any]:
    """Per model and field: unit count, total length and mean raw idf (G's BM25 floors negative idf at
    EPSILON times the mean idf of its terms). Stored in meta, with a generation counter."""
    df_text, df_fig = _df(con, "rtext"), _df(con, "rfig")
    df_all = df_text + df_fig
    stats: dict[str, Any] = {}
    for model, df, kinds in (("text", df_text, "figure IS NULL"), ("all", df_all, "1")):
        n, lw, lh = con.execute(f"SELECT COUNT(*), TOTAL(len_w), TOTAL(len_h) FROM units WHERE {kinds}").fetchone()
        for field, total in (("w", lw), ("h", lh)):
            idf = [math.log(n - f + 0.5) - math.log(f + 0.5) for t, f in df.items() if is_han(t) == (field == "h")]
            stats[f"{model}.{field}"] = {"n": n, "total": total, "mean_idf": sum(idf) / len(idf) if idf else 0.0}
    gen = int((con.execute("SELECT value FROM meta WHERE key = 'generation'").fetchone() or ["0"])[0]) + 1
    con.execute("INSERT OR REPLACE INTO meta VALUES('stats', ?)", (json.dumps(stats),))
    con.execute("INSERT OR REPLACE INTO meta VALUES('generation', ?)", (str(gen),))
    return stats


def update(name: str, path: Path | None = None) -> int:
    """(Re)index one registered document and refresh the statistics; returns its number of units.
    The incremental entry point for ingestion: call it after the document's bundle and sidecars exist."""
    with closing(connect(path, create=True)) as con:
        if diff := mismatch(con):
            raise RuntimeError(f"library index built with other settings {diff} (index, here): rebuild every "
                               "document with python -m tglw.corpus build")
        with con:
            remove(con, name)
            n = add(con, name)
            refresh_stats(con)
    return n


def drop(name: str, path: Path | None = None) -> int:
    """Remove one document from the index; returns how many units it had."""
    with closing(connect(path)) as con:
        with con:
            n = remove(con, name)
            refresh_stats(con)
    return n


def build(names: Iterable[str] | None = None, path: Path | None = None, only_stale: bool = False,
          log=print) -> dict[str, int]:
    """Index the given (default: every cached) documents in one transaction per document; statistics
    once at the end. Documents no longer registered are dropped when no names are given. An index built
    with other settings is emptied first when every document is built, refused otherwise."""
    everything = names is None
    names = list(names) if names is not None else [d.name for d in registry.docs()]
    out: dict[str, int] = {}
    with closing(connect(path, create=True)) as con:
        if diff := mismatch(con):
            if not everything:
                raise RuntimeError(f"library index built with other settings {diff} (index, here): build every document")
            with con:
                _reset(con)
            log(f"settings changed {diff}: index emptied")
        have = dict(con.execute("SELECT name, sources FROM docs"))
        if everything:
            for gone in sorted(set(have) - set(names)):
                with con:
                    remove(con, gone)
                log(f"dropped {gone}")
        for name in names:
            if only_stale and name in have and json.loads(have[name]) == sources(name):
                continue
            t = time.time()
            with con:
                remove(con, name)
                out[name] = add(con, name)
            log(f"{name}: {out[name]} units, {time.time() - t:.1f} s")
        with con:
            refresh_stats(con)
            con.execute("INSERT INTO ftext(ftext) VALUES('optimize')")
            con.execute("INSERT INTO ffig(ffig) VALUES('optimize')")
    return out


def stale(path: Path | None = None) -> dict[str, str]:
    """Documents whose index rows no longer match their sources: name -> reason."""
    with closing(connect(path)) as con:
        have = {n: json.loads(s) for n, s in con.execute("SELECT name, sources FROM docs")}
    out = {}
    for d in registry.docs():
        if d.name not in have:
            out[d.name] = "not indexed"
        elif (now := sources(d.name)) != have[d.name]:
            out[d.name] = "changed: " + ", ".join(k for k in now if now[k] != have[d.name].get(k))
    for name in sorted(set(have) - {d.name for d in registry.docs()}):
        out[name] = "not registered"
    return out
