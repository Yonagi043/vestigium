"""Multiset token matching with an OCR-tolerant fuzzy fallback."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from rapidfuzz import fuzz, process

FUZZY_MIN_LEN = 4
FUZZY_CUTOFF = 80.0


def consume(toks: Sequence[str], bag: Counter[str], fuzzy: bool = True,
            first: Sequence[bool] | None = None) -> list[bool]:
    """Mark each token found in `bag`, consuming one count per hit: exact first, then fuzzy.
    Tokens flagged in `first` claim counts before the others, so when a word runs short the
    unflagged occurrences are the ones left unmatched."""
    order = sorted(range(len(toks)), key=lambda i: not first[i]) if first else range(len(toks))
    left = Counter({k: v for k, v in bag.items() if v > 0})
    hit = [False] * len(toks)
    for i in order:
        if left[toks[i]] > 0:
            left[toks[i]] -= 1
            hit[i] = True
    if fuzzy:
        _fuzzy(toks, hit, left, order)
    return hit


def _fuzzy(toks: Sequence[str], hit: list[bool], left: Counter[str], order: Sequence[int]) -> None:
    keys = [k for k, v in left.items() if v > 0 and len(k) >= FUZZY_MIN_LEN]
    for i in order:
        t = toks[i]
        if hit[i] or len(t) < FUZZY_MIN_LEN or not keys:
            continue
        best = process.extractOne(t, keys, scorer=fuzz.ratio, score_cutoff=FUZZY_CUTOFF)
        if best is None:
            continue
        key = best[0]
        left[key] -= 1
        hit[i] = True
        if left[key] == 0:
            keys.remove(key)

