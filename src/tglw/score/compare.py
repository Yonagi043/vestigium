"""Paired comparison of the conditions in a graded stage (PROTOCOL section 7).

Per question and condition the strict score is the share of required facets judged correct; the
lenient score also counts partial. Unanswerable questions stay out of the paired accuracy and are
only described. Two conditions are compared question by question: the mean difference, a 95%
interval from resampling questions (bootstrap, fixed seed) and a two-sided sign test. Costs are
compared the same way, as the ratio of the two conditions' totals over the same questions.
Breakdowns: document class, question type (a question counts under each of its types), whole text
over a 200k-token window (o200k tokens, as in `estimate`), and scanned documents.
"""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from tglw.common import registry
from tglw.common.paths import DATA

from . import costs, plan, summary
from .sheet import VERDICTS

# the largest whole text (o200k tokens) a 200k-token context holds next to Claude Code's fixed overhead
# of 29,568 tokens, at 1.529 Claude tokens per o200k token (as tglw.score.estimate computes it)
WINDOW_O200K = int((200_000 - 29_568) / 1.529)

PAIRS = (("T", "P"), ("T", "W"), ("P", "W"), ("T", "V"), ("V", "W"))
REPS = 10_000


def scores(rows: Iterable[dict[str, str]]) -> dict[tuple[str, str], dict]:
    """(condition, question) -> scores of its graded sheet; sheets with unfilled rows are left out."""
    by: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by[(r["condition"], r["question_id"])].append(r)
    out: dict[tuple[str, str], dict] = {}
    for key, rs in by.items():
        vs = [(r, summary.verdict(r["verdict"])) for r in rs]
        if any(v is None for _, v in vs):
            continue
        if any(r["facet_id"] == "abstain" for r in rs):
            out[key] = {"unanswerable": True, "abstain": vs[0][1]}
            continue
        req = [v for r, v in vs if r["required"] == "yes"]
        if not req:
            continue
        c, p = req.count("correct"), req.count("partial")
        out[key] = {"unanswerable": False, "strict": c / len(req), "lenient": (c + p) / len(req),
                    "full_strict": c == len(req), "full_lenient": c + p == len(req),
                    "fabricated": sum(v == "fabricated" for _, v in vs)}
    return out


def bootstrap(values: Sequence[float], stat: Callable[[list[int]], float], reps: int = REPS,
              seed: int = 0) -> tuple[float, float]:
    """95% percentile interval of stat(indices) over resampled index lists."""
    rng = random.Random(seed)
    n = len(values)
    got = sorted(stat([rng.randrange(n) for _ in range(n)]) for _ in range(reps))
    return got[int(0.025 * reps)], got[int(0.975 * reps) - 1]


def sign_test(diffs: Sequence[float]) -> tuple[int, int, float]:
    """(questions where the first condition scored higher, lower, two-sided p); ties are dropped."""
    pos, neg = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    n = pos + neg
    if not n:
        return pos, neg, 1.0
    p = 2 * sum(math.comb(n, i) for i in range(min(pos, neg) + 1)) / 2 ** n
    return pos, neg, min(1.0, p)


def paired(sc: dict[tuple[str, str], dict], a: str, b: str, key: str) -> dict:
    qs = sorted(q for (c, q), s in sc.items() if c == a and not s["unanswerable"] and (b, q) in sc)
    d = [sc[(a, q)][key] - sc[(b, q)][key] for q in qs]
    if not d:
        return {"n": 0}
    lo, hi = bootstrap(d, lambda idx: sum(d[i] for i in idx) / len(idx))
    pos, neg, p = sign_test(d)
    return {"n": len(qs), "a": statistics.mean(sc[(a, q)][key] for q in qs),
            "b": statistics.mean(sc[(b, q)][key] for q in qs), "diff": statistics.mean(d), "lo": lo, "hi": hi,
            "pos": pos, "neg": neg, "p": p}


def paired_cost(runs: Sequence[dict], a: str, b: str, field: str) -> dict:
    by = {(r["condition"], r["question_id"]): r for r in runs if r.get(field) is not None}
    qs = sorted(q for (c, q) in by if c == a and (b, q) in by)
    xa, xb = [by[(a, q)][field] for q in qs], [by[(b, q)][field] for q in qs]
    if not qs or not sum(xb):
        return {"n": 0}
    lo, hi = bootstrap(xa, lambda idx: sum(xa[i] for i in idx) / (sum(xb[i] for i in idx) or math.nan))
    return {"n": len(qs), "sum_a": sum(xa), "sum_b": sum(xb), "ratio": sum(xa) / sum(xb), "lo": lo, "hi": hi,
            "a_lower": sum(x < y for x, y in zip(xa, xb))}


def kappa(first: Iterable[dict[str, str]], second: Iterable[dict[str, str]]) -> dict:
    """Agreement between two gradings of the same sheets, row by row (run code, facet)."""
    a = {(r["run_id"], r["facet_id"]): summary.verdict(r["verdict"]) for r in first}
    b = {(r["run_id"], r["facet_id"]): summary.verdict(r["verdict"]) for r in second}
    keys = sorted(k for k in a if k in b and a[k] and b[k])
    if not keys:
        return {"n": 0}

    def k_of(cats: Sequence[str], f: Callable[[str], str]) -> tuple[float, float]:
        po = sum(f(a[k]) == f(b[k]) for k in keys) / len(keys)
        pe = sum((sum(f(a[k]) == c for k in keys) / len(keys)) * (sum(f(b[k]) == c for k in keys) / len(keys))
                 for c in cats)
        return po, (po - pe) / (1 - pe) if pe < 1 else 1.0

    po, k5 = k_of(VERDICTS, lambda v: v)
    po2, k2 = k_of(("correct", "other"), lambda v: v if v == "correct" else "other")
    changed = [(k, a[k], b[k]) for k in keys if a[k] != b[k]]
    return {"n": len(keys), "agree": po, "kappa": k5, "agree_correct": po2, "kappa_correct": k2, "changed": changed}


FULLTEXT_INDEX = DATA / "K" / "fulltext" / "index.csv"


def doc_tokens(index: Path = FULLTEXT_INDEX) -> dict[str, int]:
    """Whole-text o200k tokens per document from a `fulltext` index.csv ({} when there is none)."""
    if not index.exists():
        return {}
    with open(index, newline="") as fh:
        return {r["doc"]: int(r["tokens"]) for r in csv.DictReader(fh)}


def is_scan(doc: str) -> bool:
    """At least 3 of the first 6 pages carry an image covering the page (about 7 x 9.5 inches or
    more at its own resolution, either way round), as page scans do; a cover photo alone does not
    count."""
    done = subprocess.run(["pdfimages", "-f", "1", "-l", "6", "-list", str(registry.get(doc).pdf_path)],
                          capture_output=True, text=True)
    pages = set()
    for line in done.stdout.splitlines()[2:]:
        f = line.split()
        if len(f) > 13 and f[12].isdigit() and f[13].isdigit() and int(f[12]) and int(f[13]):
            short, long = sorted((int(f[3]) / int(f[12]), int(f[4]) / int(f[13])))
            if short >= 7 and long >= 9.5:
                pages.add(f[0])
    return len(pages) >= 3


def groups(rows: Sequence[dict[str, str]], types: dict[str, list[str]],
           index: Path = FULLTEXT_INDEX) -> dict[str, dict[str, list[str]]]:
    """Breakdown name -> question -> group labels."""
    doc = {r["question_id"]: r["doc"] for r in rows}
    tokens = doc_tokens(index)
    window = WINDOW_O200K
    scans = {d: is_scan(d) for d in set(doc.values())}
    return {
        "文档类": {q: [registry.get(d).cls] for q, d in doc.items()},
        "题型（一题可属多类）": {q: types.get(q) or ["?"] for q in doc},
        "整篇是否超过 200k 窗口": {q: ["未知" if d not in tokens else "超过" if tokens[d] > window else "不超过"]
                             for q, d in doc.items()},
        "扫描件": {q: ["是" if scans[d] else "否"] for q, d in doc.items()},
    }


def _pct(x: float) -> str:
    return f"{x:.1%}"


def report(rows: Sequence[dict[str, str]], plan_rows: Sequence[dict[str, str]], records: Path,
           types: dict[str, list[str]], regrade: Sequence[dict[str, str]] = (), per_question: bool = True,
           index: Path = FULLTEXT_INDEX) -> str:
    sc = scores(rows)
    runs = costs.valid_runs(plan_rows, records)
    lines = ["## 1. 准确率（按要点汇总，`summarize` 的口径）", "", summary.report(rows).rstrip(), "",
             "## 2. 配对比较：准确率（以题为单位）", "",
             "每题得分 = 必答要点里判“对”（严格）或“对、部分对”（宽松）的比例；只含这两种条件都判完的可回答题。"
             f"差值 = 前者减后者，区间是按题重抽样 {REPS:,} 次的 95% 自助法区间；符号检验去掉平局。", "",
             "| 比较 | 口径 | 题数 | 前者均分 | 后者均分 | 差值 | 95% 区间 | 前者高/低（题） | 符号检验 p |",
             "|---|---|---|---|---|---|---|---|---|"]
    for a, b in PAIRS:
        for key, label in (("strict", "严格"), ("lenient", "宽松")):
            s = paired(sc, a, b, key)
            if s["n"]:
                lines.append(f"| {a} − {b} | {label} | {s['n']} | {_pct(s['a'])} | {_pct(s['b'])} | {s['diff']:+.1%} | "
                             f"[{s['lo']:+.1%}, {s['hi']:+.1%}] | {s['pos']}/{s['neg']} | {s['p']:.3f} |")
    lines += ["", "## 3. 花费和用时", "", "每次运行的数字来自任务 J 的 measure（Claude token）和运行器记录的时间；"
              "取每个（条件，题）最后一次有效尝试。", "", costs.table(runs, per_question).rstrip(), "",
              "### 配对比较：花费（同一批题上两种条件的合计之比）", "",
              "| 比较 | 指标 | 题数 | 前者合计 | 后者合计 | 比值 | 95% 区间 | 前者更少的题 |", "|---|---|---|---|---|---|---|---|"]
    for a, b in PAIRS:
        for field, label in (("weighted_input", "加权输入"), ("prompt_total", "提示合计"), ("output", "输出"),
                             ("host_seconds", "主机秒"), ("api_seconds", "API 秒"), ("tool_calls", "工具调用")):
            s = paired_cost(runs, a, b, field)
            if s["n"]:
                lines.append(f"| {a} / {b} | {label} | {s['n']} | {s['sum_a']:,.0f} | {s['sum_b']:,.0f} | "
                             f"{s['ratio']:.2f} | [{s['lo']:.2f}, {s['hi']:.2f}] | {s['a_lower']} |")
    lines += ["", "## 4. 分组", ""]
    cost_by = {(r["condition"], r["question_id"]): r for r in runs}
    for name, labels in groups(plan_rows, types, index).items():
        lines += [f"### 按{name}", "", "| 组 | 条件 | 可回答题 | 严格均分 | 宽松均分 | 整题全对（严格） | 编造要点 | "
                  "不可回答题答“没有” | 加权输入合计 | 主机秒合计 |", "|---|---|---|---|---|---|---|---|---|---|"]
        members: dict[str, list[str]] = defaultdict(list)
        for q, ls in labels.items():
            for label in ls:
                members[label].append(q)
        for label in sorted(members):
            for c in plan.CONDITIONS:
                ans = [sc[(c, q)] for q in members[label] if (c, q) in sc and not sc[(c, q)]["unanswerable"]]
                un = [sc[(c, q)] for q in members[label] if (c, q) in sc and sc[(c, q)]["unanswerable"]]
                cr = [cost_by[(c, q)] for q in members[label] if (c, q) in cost_by]
                if not ans and not un:
                    continue
                strict = _pct(statistics.mean(s["strict"] for s in ans)) if ans else "-"
                lenient = _pct(statistics.mean(s["lenient"] for s in ans)) if ans else "-"
                lines.append(f"| {label} | {c} | {len(ans)} | {strict} | {lenient} | "
                             f"{sum(s['full_strict'] for s in ans)}/{len(ans)} | {sum(s['fabricated'] for s in ans)} | "
                             f"{sum(s['abstain'] == 'correct' for s in un)}/{len(un)} | "
                             f"{sum(r['weighted_input'] or 0 for r in cr):,} | {sum(r['host_seconds'] or 0 for r in cr):,.0f} |")
        lines.append("")
    if regrade:
        k = kappa(rows, regrade)
        lines += ["## 5. 判分一致性（换新判分代理重判的部分）", ""]
        if k["n"]:
            lines += [f"- 重判的要点 {k['n']} 个：五类判定一致 {_pct(k['agree'])}，Cohen's kappa {k['kappa']:.2f}；"
                      f"只分“对 / 其他”时一致 {_pct(k['agree_correct'])}，kappa {k['kappa_correct']:.2f}。",
                      f"- 判定不同的 {len(k['changed'])} 个，按“第一次 → 重判”计数：" +
                      ("；".join(f"{x} → {y} {n} 个" for (x, y), n in sorted(Counter((x, y) for _, x, y in
                                                                               k["changed"]).items())) or "无") + "。", ""]
    return "\n".join(lines) + "\n"


def load_types(paths: Iterable[Path]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in paths:
        for line in Path(p).read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["id"]] = list(rec.get("types") or [])
    return out
