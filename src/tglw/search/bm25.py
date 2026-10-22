"""Okapi BM25 with weighted query terms; with unit weights it scores exactly like rank_bm25's BM25Okapi."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence


class BM25:
    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in corpus]
        self.len = [len(d) for d in corpus]
        n = len(corpus)
        self.avgdl = sum(self.len) / n if n else 0.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        idf = {t: math.log(n - f + 0.5) - math.log(f + 0.5) for t, f in df.items()}
        floor = epsilon * (sum(idf.values()) / len(idf)) if idf else 0.0
        self.idf = {t: (v if v >= 0 else floor) for t, v in idf.items()}

    def scores(self, query: Mapping[str, float] | Sequence[str]) -> list[float]:
        """Scores of every document; a sequence query counts repeated terms repeatedly, a mapping
        gives each term a weight."""
        weights = query if isinstance(query, Mapping) else Counter(query)
        out = [0.0] * len(self.tf)
        if not self.avgdl:
            return out
        for t, w in weights.items():
            idf = self.idf.get(t)
            if not idf:
                continue
            for i, c in enumerate(self.tf):
                f = c.get(t)
                if f:
                    norm = self.k1 * (1 - self.b + self.b * self.len[i] / self.avgdl)
                    out[i] += w * idf * f * (self.k1 + 1) / (f + norm)
        return out


class SplitBM25:
    """Two BM25 fields over one corpus, summed: tokens for which `side(token)` is true (Han characters
    and pairs) and the rest, each with its own lengths and statistics. Adding the Han tokens of a page
    then leaves the scores of its other words as they were (one length normalisation for both would
    make every page with Chinese text "longer" for an English query)."""

    def __init__(self, corpus: Sequence[Sequence[str]], side) -> None:
        self.parts = [BM25([[t for t in d if side(t) == flag] for d in corpus]) for flag in (False, True)]
        self.tf = [a + b for a, b in zip(self.parts[0].tf, self.parts[1].tf)]
        self.idf = {**self.parts[0].idf, **self.parts[1].idf}

    def scores(self, query: Mapping[str, float] | Sequence[str]) -> list[float]:
        a, b = (p.scores(query) for p in self.parts)
        return [x + y for x, y in zip(a, b)]
