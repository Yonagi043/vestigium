"""Aggregate filled grading sheets into accuracy per condition (and per split).

Strict counts a required facet only when judged correct; lenient also counts partial. Optional
facets are reported apart and never enter accuracy. A question is fully right (strict/lenient)
when every required facet is. Unanswerable questions are reported apart: correct abstentions,
fabrications, and other verdicts. Rows without a verdict are listed and left out of every count;
the CLI refuses to write a summary while any remain unless told otherwise.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .sheet import COLUMNS, MD_COLUMNS

ALIASES = {
    "correct": "correct", "对": "correct", "✓": "correct",
    "partial": "partial", "部分对": "partial", "部分": "partial",
    "wrong": "wrong", "错": "wrong", "✗": "wrong",
    "missing": "missing", "没写": "missing", "漏": "missing", "漏写": "missing",
    "fabricated": "fabricated", "编造": "fabricated",
}
_PIPE = re.compile(r"(?<!\\)\|")
_NEEDED = ("run_id", "condition", "question_id", "split", "facet_id", "required", "verdict")


def verdict(raw: str) -> str | None:
    v = (raw or "").strip().lower()
    if not v:
        return None
    if v not in ALIASES:
        raise ValueError(f"unknown verdict {raw!r}; use one of {sorted(set(ALIASES.values()))}")
    return ALIASES[v]


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if rows and (missing := [c for c in _NEEDED if c not in rows[0]]):
        raise ValueError(f"{path}: missing columns {missing}")
    return rows


def read_markdown(path: Path) -> list[dict[str, str]]:
    """Rows of a sheet's Markdown table; run metadata come from the sibling CSV."""
    sibling = path.with_suffix(".csv")
    meta = read_csv(sibling)[0] if sibling.exists() else {}
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("| ") or line.startswith("| 要点") or set(line) <= {"|", "-", " "}:
            continue
        cells = [c.strip().replace("\\|", "|") for c in _PIPE.split(line)[1:-1]]
        if len(cells) != len(MD_COLUMNS):
            continue
        rows.append({k: meta.get(k, "") for k in COLUMNS} | dict(zip(MD_COLUMNS, cells)))
    return rows


def read_sheets(paths: Iterable[Path]) -> list[dict[str, str]]:
    """Every row once: a run graded twice (e.g. its .md and its .csv both given) is an error."""
    rows: list[dict[str, str]] = []
    for p in map(Path, paths):
        rows += read_markdown(p) if p.suffix == ".md" else read_csv(p)
    dup = [k for k, n in Counter((r["run_id"], r["facet_id"]) for r in rows).items() if n > 1]
    if dup:
        raise ValueError(f"rows given twice: {dup[:5]}")
    return rows


def unblind(rows: Sequence[dict[str, str]], key: Path) -> list[dict[str, str]]:
    codes = {r["code"]: r for r in read_csv_plain(key)}
    unknown = sorted({r["run_id"] for r in rows} - set(codes))
    if unknown:
        raise ValueError(f"codes not in {key}: {unknown[:5]}")
    return [r | {"run_id": codes[r["run_id"]]["run_id"], "condition": codes[r["run_id"]]["condition"]} for r in rows]


def read_csv_plain(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Tally:
    runs: set[str] = field(default_factory=set)
    answerable: set[str] = field(default_factory=set)
    required: int = 0
    correct: int = 0
    partial: int = 0
    fabricated: int = 0
    optional: int = 0
    optional_correct: int = 0
    unanswerable: int = 0
    abstained: int = 0
    unanswerable_fabricated: int = 0
    unanswerable_other: int = 0
    full_strict: int = 0
    full_lenient: int = 0
    unfilled: list[str] = field(default_factory=list)

    def rate(self, num: int, den: int) -> str:
        return f"{num / den:.1%}" if den else "-"


def tally(rows: Sequence[dict[str, str]], key: str = "condition") -> dict[str, Tally]:
    out: dict[str, Tally] = defaultdict(Tally)
    by_run: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_run[(r[key], r["run_id"])].append(r)
    for (group, run), rs in by_run.items():
        t = out[group]
        t.runs.add(run)
        verdicts = [(r, verdict(r["verdict"])) for r in rs]
        t.unfilled += [f"{run}/{r['facet_id']}" for r, v in verdicts if v is None]
        if any(r["facet_id"] == "abstain" for r in rs):
            v = verdicts[0][1]
            if v is None:
                continue
            t.unanswerable += 1
            t.abstained += v == "correct"
            t.unanswerable_fabricated += v == "fabricated"
            t.unanswerable_other += v not in ("correct", "fabricated")
            continue
        req = [v for r, v in verdicts if r["required"] == "yes" and v is not None]
        opt = [v for r, v in verdicts if r["required"] != "yes" and v is not None]
        complete = all(v is not None for r, v in verdicts if r["required"] == "yes")
        t.required += len(req)
        t.correct += req.count("correct")
        t.partial += req.count("partial")
        t.fabricated += sum(v == "fabricated" for _, v in verdicts)
        t.optional += len(opt)
        t.optional_correct += opt.count("correct")
        if complete and req:
            t.answerable.add(run)
            t.full_strict += all(v == "correct" for v in req)
            t.full_lenient += all(v in ("correct", "partial") for v in req)
    return dict(out)


def report(rows: Sequence[dict[str, str]]) -> str:
    lines = ["| 条件 | 题集 | 运行数 | 已判必答要点 | 严格准确率 | 宽松准确率 | 整题全对（严格/宽松） | 编造要点 |"
             " 加分要点答对 | 不可回答：答“没有” | 不可回答：编造 | 不可回答：其他 | 未判定 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        groups[(r["condition"], r["split"])].append(r)
        groups[(r["condition"], "全部")].append(r)
    unfilled: list[str] = []
    for (cond, split), rs in sorted(groups.items()):
        t = tally(rs)[cond]
        n = len(t.answerable)
        if split == "全部":
            unfilled += t.unfilled
        lines.append(
            f"| {cond} | {split} | {len(t.runs)} | {t.required} | {t.rate(t.correct, t.required)} | "
            f"{t.rate(t.correct + t.partial, t.required)} | {t.full_strict}/{n}，{t.full_lenient}/{n} | "
            f"{t.fabricated} | {t.optional_correct}/{t.optional} | {t.abstained}/{t.unanswerable} | "
            f"{t.unanswerable_fabricated} | {t.unanswerable_other} | {len(t.unfilled)} |")
    if unfilled:
        lines += ["", "未判定的行（不计入任何一列）：" + "、".join(sorted(unfilled))]
    return "\n".join(lines) + "\n"
