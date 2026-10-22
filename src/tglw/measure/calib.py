"""Check the round-trip model on Claude Code logs and estimate its fixed overhead F.

Copied turns (a resumed or forked session repeating earlier calls) are left out. For consecutive
API calls in one session, the model says the prompt grows by what the previous
call output plus what came back in between (tool results, user text, rendered attachments):
P_t - P_{t-1} ~= output_{t-1} + added_t. Turns after a compaction or with images are skipped.
F is the first call's prompt: system prompt, tool definitions, project instructions, and the
opening user message.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from .log import Session
from .summary import _seconds, compactions


def growth_rows(s: Session) -> list[dict[str, int]]:
    dropped = set(compactions(s))
    rows = []
    for prev, t in zip(s.turns, s.turns[1:]):
        if t.index in dropped or t.added_images or t.index in s.copied or prev.index in s.copied:
            continue
        rows.append({"growth": t.prompt - prev.prompt, "output": prev.output, "added": t.added_tokens})
    return rows


def tokenizer_factor(rows: Sequence[dict[str, int]], floor: int = 200) -> float | None:
    """Claude tokens per o200k token for added context: median of (growth - output) / added."""
    ratios = [(r["growth"] - r["output"]) / r["added"] for r in rows if r["added"] >= floor]
    return round(statistics.median(ratios), 3) if ratios else None


def resend_check(s: Session, k: float) -> dict[str, Any] | None:
    """Actual sum of prompts after the first vs the model: each prompt = previous prompt + previous
    output + k x added context. The model re-anchors on the actual prompt after a compaction or
    an image (whose token cost is not measured here)."""
    if len(s.turns) < 2 or s.copied:
        return None
    dropped = set(compactions(s))
    model = actual = 0
    context = s.turns[0].prompt
    for prev, t in zip(s.turns, s.turns[1:]):
        context = t.prompt if t.index in dropped or t.added_images else context + prev.output + k * t.added_tokens
        model += context
        actual += t.prompt
    if not actual:
        return None
    return {"turns": len(s.turns), "actual": actual, "model": round(model), "ratio": round(model / actual, 3)}


def calibrate(sessions: Sequence[Session]) -> dict[str, Any]:
    fresh = [s for s in sessions if s.turns and 0 not in s.copied]
    mains = [s for s in fresh if s.kind == "main"]
    subs = [s for s in fresh if s.kind == "subagent"]
    flows = [s for s in fresh if s.kind == "workflow"]
    rows = [r for s in sessions for r in growth_rows(s)]
    k = tokenizer_factor(rows) or 1.0
    residual = [r["growth"] - r["output"] - k * r["added"] for r in rows]
    checks = [c for s in sessions if (c := resend_check(s, k))]
    first = lambda ss: sorted(s.turns[0].prompt for s in ss)  # noqa: E731
    snap = lambda key: sorted(s.snapshot_tokens[key] for s in sessions if key in s.snapshot_tokens)  # noqa: E731
    return {
        "sessions": {"main": len(mains), "subagent": len(subs), "workflow": len(flows),
                     "resumed_or_forked": sum(bool(s.copied) for s in sessions)},
        "F_first_prompt": {"main": _dist(first(mains)), "subagent": _dist(first(subs)), "workflow": _dist(first(flows))},
        "F_parts_o200k": {"system_prompt": _dist(snap("systemPrompt")), "tool_definitions": _dist(snap("tools"))},
        "growth_check": {
            "turn_pairs": len(rows),
            "tokenizer_factor": k,
            "median_growth": _median([r["growth"] for r in rows]),
            "median_residual": _median(residual),
            "median_abs_residual": _median([abs(x) for x in residual]),
        },
        "resend_sum_check": {
            "sessions": len(checks),
            "median_model_over_actual": _median([c["ratio"] for c in checks]),
            "range": [min((c["ratio"] for c in checks), default=None), max((c["ratio"] for c in checks), default=None)],
        },
        "cache": {
            "read_share_of_prompt": _share(sessions, "cache_read"),
            "write_share_of_prompt": _share(sessions, "cache_creation"),
            "uncached_share_of_prompt": _share(sessions, "input"),
        },
        "seconds_per_turn": _dist(sorted(
            round(w / len(s.turns), 1) for s in mains if (w := _seconds(s.first_ts, s.last_ts)))),
        "active_seconds_between_calls": _dist(sorted(active_gaps(mains))),
    }


def active_gaps(sessions: Sequence[Session], idle: float = 300.0) -> list[float]:
    """Seconds between consecutive API calls in a session, leaving out gaps longer than `idle`
    (a person reading or away): model latency plus tool execution per round trip."""
    gaps = []
    for s in sessions:
        for prev, t in zip(s.turns, s.turns[1:]):
            g = _seconds(prev.ts, t.ts)
            if g is not None and 0 <= g <= idle:
                gaps.append(g)
    return gaps


def _share(sessions: Sequence[Session], key: str) -> float | None:
    own = [t for s in sessions for t in s.turns if t.index not in s.copied]
    total = sum(t.prompt for t in own)
    return round(sum(getattr(t, key) for t in own) / total, 4) if total else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _dist(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "min": values[0], "median": statistics.median(values), "max": values[-1]}
