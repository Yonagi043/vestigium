"""Choose one printed label per PDF page: a Viterbi pass over runs of consecutive numbering.

A run is (scheme, prefix, value - pdf_page): pages in one run are numbered consecutively. Staying
in a run is free, entering a run costs SWITCH_COST, and each page earns the evidence weight of the
run's reading on that page. Labels are then interpolated inside runs and back-filled to value 1
when a run's first pages are unnumbered (e.g. a chapter's opening page).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .candidates import Cand
from .labels import PageNum

SWITCH_COST = 0.9
MAX_BACKFILL = 4
HIGH_RUN_EVIDENCE = 3
SHORT_RUN = 2
MAX_ROUNDS = 5

Key = tuple[str, str, int]


@dataclass(slots=True)
class Assigned:
    pdf_page: int
    num: PageNum | None = None
    source: str | None = None
    confidence: str | None = None
    raw: str | None = None
    ref: str | None = None
    run: int | None = None


@dataclass(slots=True)
class Run:
    key: Key
    first: int
    last: int
    evidence: list[int]
    upper: bool


def evidence(cands: list[Cand]) -> dict[int, dict[Key, tuple[float, Cand]]]:
    """Per page and run key: summed best weight per channel (Docling, text layer) and best cand."""
    best: dict[tuple[int, Key, str], Cand] = {}
    for c in cands:
        ch = "textlayer" if c.channel == "textlayer" else "docling"
        k = (c.page, c.key, ch)
        if k not in best or c.weight > best[k].weight:
            best[k] = c
    out: dict[int, dict[Key, tuple[float, Cand]]] = defaultdict(dict)
    for (page, key, _), c in best.items():
        score, top = out[page].get(key, (0.0, c))
        if (c.channel != "textlayer", c.weight) > (top.channel != "textlayer", top.weight):
            top = c
        out[page][key] = (score + c.weight, top)
    return out


def viterbi(n_pages: int, ev: dict[int, dict[Key, tuple[float, Cand]]]) -> list[Key | None]:
    totals: Counter[Key] = Counter()
    for page in ev.values():
        for k, (s, _) in page.items():
            totals[k] += s
    keys = [k for k, s in totals.items() if s > SWITCH_COST]
    dp: dict[Key | None, float] = {None: 0.0}
    back: list[dict[Key | None, Key | None]] = []
    for p in range(1, n_pages + 1):
        best_k = max(dp, key=lambda k: (dp[k], k is None))
        best = dp[best_k]
        new: dict[Key | None, float] = {None: best}
        bp: dict[Key | None, Key | None] = {None: best_k}
        page_ev = ev.get(p, {})
        for k in keys:
            stay = dp.get(k, float("-inf"))
            if stay >= best - SWITCH_COST:
                new[k], bp[k] = stay, k
            else:
                new[k], bp[k] = best - SWITCH_COST, best_k
            if k in page_ev:
                new[k] += page_ev[k][0]
        back.append(bp)
        dp = new
    state = max(dp, key=lambda k: (dp[k], k is None))
    path: list[Key | None] = [None] * n_pages
    for p in range(n_pages, 0, -1):
        path[p - 1] = state
        state = back[p - 1][state]
    return path


def _runs(path: list[Key | None], ev: dict[int, dict[Key, tuple[float, Cand]]]) -> list[Run]:
    runs: list[Run] = []
    p = 1
    while p <= len(path):
        key = path[p - 1]
        end = p
        while end < len(path) and path[end] == key:
            end += 1
        if key is not None:
            pages = [q for q in range(p, end + 1) if key in ev.get(q, {})]
            if pages:
                uppers = [ev[q][key][1].num.upper for q in pages]
                runs.append(Run(key, pages[0], pages[-1], pages, sum(uppers) * 2 > len(uppers)))
        p = end + 1
    return runs


def _num(run: Run, page: int) -> PageNum | None:
    scheme, prefix, offset = run.key
    v = page + offset
    return PageNum(scheme, v, prefix, run.upper) if v >= 1 else None


def _labels(run: Run) -> set[str]:
    return {n.label for p in range(run.first, run.last + 1) if (n := _num(run, p))}


def _restarts(run: Run, runs: list[Run]) -> bool:
    """Run counts from 1, possibly after a few unnumbered pages that no other run claims."""
    first = _num(run, run.first)
    if first is None or first.value - 1 > MAX_BACKFILL:
        return False
    start = run.first - (first.value - 1)
    return start >= 1 and not any(o is not run and o.first < run.first and o.last >= start for o in runs)


def _suspicious(runs: list[Run]) -> list[Run]:
    """Short runs that are probably misreadings: inside one longer run (e.g. OCR "340" between
    "3-39" and "3-42"), or repeating labels of the same family without restarting at 1."""
    out = []
    for i, run in enumerate(runs):
        if len(run.evidence) > SHORT_RUN:
            continue
        prev, nxt = runs[i - 1] if i else None, runs[i + 1] if i + 1 < len(runs) else None
        if prev and nxt and prev.key == nxt.key:
            out.append(run)
            continue
        family = run.key[:2]
        if not _restarts(run, runs) and any(
                o is not run and o.key[:2] == family and _labels(o) & _labels(run) for o in runs):
            out.append(run)
    return out


def _solve_runs(n_pages: int, ev: dict[int, dict[Key, tuple[float, Cand]]]) -> list[Run]:
    for _ in range(MAX_ROUNDS):
        runs = _runs(viterbi(n_pages, ev), ev)
        bad = _suspicious(runs)
        if not bad:
            break
        for run in bad:
            for p in run.evidence:
                ev[p].pop(run.key, None)
    return runs


def assign(n_pages: int, cands: list[Cand]) -> tuple[list[Assigned], list[Run]]:
    ev = evidence(cands)
    runs = _solve_runs(n_pages, ev)
    out = [Assigned(p) for p in range(1, n_pages + 1)]
    for i, run in enumerate(runs):
        ev_pages = set(run.evidence)
        for p in range(run.first, run.last + 1):
            a = out[p - 1]
            a.num, a.run = _num(run, p), i
            if p in ev_pages:
                c = ev[p][run.key][1]
                a.source, a.raw, a.ref = c.source, c.raw, c.ref
                a.confidence = "high" if len(ev_pages) >= HIGH_RUN_EVIDENCE else "medium"
            else:
                a.source, a.confidence = "inferred", "medium"
    for i, run in enumerate(runs):
        first_num = _num(run, run.first)
        gap = first_num.value - 1 if first_num else 0
        start = run.first - gap
        if 0 < gap <= MAX_BACKFILL and start >= 1 and all(out[p - 1].num is None for p in range(start, run.first)):
            for p in range(start, run.first):
                a = out[p - 1]
                a.num, a.run, a.source, a.confidence = _num(run, p), i, "inferred", "low"
    return out, runs
