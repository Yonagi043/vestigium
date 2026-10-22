"""Reference implementations of the tests PREREG.md section 7 names, so the analysis is fixed in code
before any confirm run: exact sign-flip permutation test, question-level (optionally stratified or
clustered) bootstrap bounds, document-level bootstrap interval of a ratio of totals, and the section 6
question count."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Hashable, Sequence
from fractions import Fraction

STATES_MAX = 10_000_000
ALTERNATIVES = ("two-sided", "less", "greater")


def _integers(d: Sequence[float | Fraction]) -> list[int]:
    """The d_i as integers on a common scale (their least common denominator), so sums are exact."""
    fr = [Fraction(x).limit_denominator(10_000) for x in d]
    scale = math.lcm(*(f.denominator for f in fr)) if fr else 1
    return [int(f * scale) for f in fr]


def exact_signflip(d: Sequence[float | Fraction], seed: int = 0, alternative: str = "two-sided") -> tuple[float, str]:
    """p of mean(d) = 0 under random sign flips of the non-zero d_i, and the method used: "exact" (the
    whole flip distribution, counted on integers) or "monte-carlo" (10**6 flips, once it passes 10**7
    distinct sums). alternative "less" / "greater": the mean is below / above 0."""
    if alternative not in ALTERNATIVES:
        raise ValueError(f"alternative must be one of {ALTERNATIVES}")
    xs = _integers([x for x in d if x != 0])
    if not xs:
        return 1.0, "exact"
    obs = sum(xs)
    dist: dict[int, int] = {0: 1}
    for x in xs:
        nxt: dict[int, int] = defaultdict(int)
        for total, count in dist.items():
            nxt[total + x] += count
            nxt[total - x] += count
        dist = nxt
        if len(dist) > STATES_MAX:
            return _monte_carlo(xs, obs, seed, alternative), "monte-carlo"
    return sum(c for t, c in dist.items() if _extreme(t, obs, alternative)) / 2 ** len(xs), "exact"


def _extreme(t: int, obs: int, alternative: str) -> bool:
    return abs(t) >= abs(obs) if alternative == "two-sided" else t <= obs if alternative == "less" else t >= obs


def _monte_carlo(xs: Sequence[int], obs: int, seed: int, alternative: str, reps: int = 1_000_000) -> float:
    rng = random.Random(seed)
    hits = sum(_extreme(sum(x if rng.random() < 0.5 else -x for x in xs), obs, alternative) for _ in range(reps))
    return (hits + 1) / (reps + 1)


def bootstrap_means(d: Sequence[float], seed: int, reps: int = 10_000, strata: Sequence[Hashable] | None = None,
                    clusters: Sequence[Hashable] | None = None) -> list[float]:
    """Sorted means of `reps` resamples of d. Questions (or whole clusters, when given: e.g. documents)
    are drawn with replacement, within each stratum when given (e.g. text / visual questions), so every
    stratum keeps its number of questions or clusters."""
    n = len(d)
    strata = list(strata) if strata is not None else [""] * n
    clusters = list(clusters) if clusters is not None else list(range(n))
    groups: dict[Hashable, dict[Hashable, list[float]]] = defaultdict(lambda: defaultdict(list))
    for x, s, c in zip(d, strata, clusters, strict=True):
        groups[s][c].append(x)
    plan = [[groups[s][c] for c in sorted(groups[s])] for s in sorted(groups)]
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        total = count = 0.0
        for members in plan:
            for _ in members:
                picked = members[rng.randrange(len(members))]
                total += sum(picked)
                count += len(picked)
        means.append(total / count)
    return sorted(means)


def bootstrap_lower(d: Sequence[float], seed: int, reps: int = 10_000, level: float = 0.975,
                    strata: Sequence[Hashable] | None = None, clusters: Sequence[Hashable] | None = None) -> float:
    """One-sided lower bound of mean(d): the (1 - level) percentile of the resampled means."""
    return bootstrap_means(d, seed, reps, strata, clusters)[int((1 - level) * reps)]


def bootstrap_interval(d: Sequence[float], seed: int, reps: int = 10_000, level: float = 0.95,
                       strata: Sequence[Hashable] | None = None,
                       clusters: Sequence[Hashable] | None = None) -> tuple[float, float]:
    """Two-sided percentile interval of mean(d); with the same seed its lower end is bootstrap_lower at
    level (1 + level) / 2."""
    means = bootstrap_means(d, seed, reps, strata, clusters)
    tail = (1 - level) / 2
    return means[int(tail * reps)], means[int((1 - tail) * reps) - 1]


def ratio_ci_by_document(rows: Sequence[tuple[str, float, float]], seed: int, reps: int = 10_000,
                         level: float = 0.95) -> tuple[float, float, float]:
    """Ratio of totals sum(a)/sum(b) over (cluster, a, b) rows, with a percentile interval from
    resampling whole clusters. The cluster is the question's document; for a cross-document question,
    its set of documents as one key (PREREG section 7)."""
    by_doc: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for doc, a, b in rows:
        by_doc[doc].append((a, b))
    docs = sorted(by_doc)
    point = sum(a for _, a, _ in rows) / sum(b for _, _, b in rows)
    rng = random.Random(seed)
    ratios = []
    for _ in range(reps):
        pick = [docs[rng.randrange(len(docs))] for _ in docs]
        a = sum(x for doc in pick for x, _ in by_doc[doc])
        b = sum(y for doc in pick for _, y in by_doc[doc])
        ratios.append(a / b)
    ratios.sort()
    tail = (1 - level) / 2
    return point, ratios[int(tail * reps)], ratios[int((1 - tail) * reps) - 1]


def needed_questions(e_d2: float, margin: float = 0.05, z: float = 1.96 + 0.8416) -> int:
    """PREREG section 6: answerable questions needed, from the direction-blind E[d^2]."""
    return int(-(-((z * e_d2 ** 0.5 / margin) ** 2) // 1))


def topup_count(n_star: int, spent: float, have: int = 27, per_question: float = 0.82, cap: float = 120.0,
                topup_cap: float = 20.0, available: int = 30) -> int:
    """PREREG section 6: answerable top-up questions to add, k = min(n* - have, what the money left
    buys at the fixed per-question price, the questions H5 delivered first)."""
    money = min(topup_cap, cap - spent)
    return max(0, min(n_star - have, int(money // per_question), available))


CREDIT = {"correct": 1.0, "partial": 0.5, "wrong": 0.0, "missing": 0.0, "fabricated": 0.0}


def merged_sheet_check(pairs: Sequence[tuple[str, str]], least: int = 8, alpha: float = 0.05) -> dict[str, float | int | bool]:
    """PREREG section 5: (first verdict on the two-model sheet, regrade verdict on the haiku-only sheet)
    for each required facet of a regraded haiku text question. Counts facets the regrade credits
    higher / lower; the check fires when at least `least` moved and the exact two-sided sign test has
    p < alpha."""
    up = sum(CREDIT[b] > CREDIT[a] for a, b in pairs)
    down = sum(CREDIT[b] < CREDIT[a] for a, b in pairs)
    n = up + down
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(min(up, down) + 1)) / 2 ** n) if n else 1.0
    return {"higher": up, "lower": down, "p": p, "fires": n >= least and p < alpha}
