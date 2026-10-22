"""Cost, time and tool calls per condition, from the meta.json of each (condition, question)'s last
valid attempt in the ledger, judged by each run's last ledger row so that a correction counts.
Token counts are Claude tokens from task J's measure: prompt total (input + cache creation + cache
read over all calls), cache-weighted input (input + 1.25 x creation + 0.1 x read), output, peak and
first prompt. Time is the host's wall clock for the run and, for headless runs, Claude Code's own
API time; the dollar figure is Claude Code's list-price estimate, not what your provider bills.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from pathlib import Path

from . import plan


def valid_runs(rows: Sequence[dict[str, str]], records: Path) -> list[dict]:
    last: dict[tuple[str, str], dict[str, str]] = {}
    for r in plan.final_rows(plan.ledger_rows(records)).values():
        if r["status"] == "valid":
            last[(r["condition"], r["question_id"])] = r
    out = []
    for row in rows:
        got = last.get((row["condition"], row["question_id"]))
        if not got:
            continue
        rid = got["run_id"]
        meta = json.loads((plan.run_records(records, got) / "meta.json").read_text())
        m, res = meta.get("measure") or {}, meta.get("result") or {}
        api = res.get("duration_api_ms")
        out.append({"condition": row["condition"], "question_id": row["question_id"], "run_id": rid,
                    **{k: m.get(k) for k in ("prompt_total", "weighted_input", "output", "peak_prompt",
                                             "first_prompt", "turns", "tool_calls", "images")},
                    "host_seconds": meta.get("host_seconds", m.get("wall_seconds")),
                    "api_seconds": api / 1000 if api is not None else None,
                    "cost_usd": res.get("total_cost_usd")})
    return out


def _vals(runs: Sequence[dict], key: str) -> list[float]:
    return [r[key] for r in runs if r.get(key) is not None]


def _sum(runs: Sequence[dict], key: str) -> float:
    return sum(_vals(runs, key))


def _med(runs: Sequence[dict], key: str) -> float:
    v = _vals(runs, key)
    return statistics.median(v) if v else float("nan")


def table(runs: Sequence[dict], per_question: bool = True) -> str:
    conds = [c for c in plan.CONDITIONS if any(r["condition"] == c for r in runs)]
    lines = ["| 条件 | 运行数 | 提示合计：总和（中位） | 加权输入：总和（中位） | 输出总和 | 峰值提示中位（最大） | 首轮提示中位 | "
             "API 调用中位 | 工具调用：总和（中位） | 图片总和 | 主机秒：总和（中位） | API 秒总和 | 标价估算 $ |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in conds:
        rs = [r for r in runs if r["condition"] == c]
        lines.append(
            f"| {c} | {len(rs)} | {_sum(rs, 'prompt_total'):,.0f}（{_med(rs, 'prompt_total'):,.0f}） | "
            f"{_sum(rs, 'weighted_input'):,.0f}（{_med(rs, 'weighted_input'):,.0f}） | {_sum(rs, 'output'):,.0f} | "
            f"{_med(rs, 'peak_prompt'):,.0f}（{max(_vals(rs, 'peak_prompt'), default=0):,.0f}） | "
            f"{_med(rs, 'first_prompt'):,.0f} | {_med(rs, 'turns'):g} | {_sum(rs, 'tool_calls'):,.0f}（{_med(rs, 'tool_calls'):g}） | "
            f"{_sum(rs, 'images'):,.0f} | {_sum(rs, 'host_seconds'):,.0f}（{_med(rs, 'host_seconds'):,.0f}） | "
            f"{_sum(rs, 'api_seconds'):,.0f} | {_sum(rs, 'cost_usd'):.2f} |")
    if not per_question:
        return "\n".join(lines) + "\n"
    qids = list(dict.fromkeys(r["question_id"] for r in runs))
    by = {(r["condition"], r["question_id"]): r for r in runs}
    lines += ["", "| 题 | " + " | ".join(f"{c}：加权输入 / 主机秒 / 工具调用" for c in conds) + " |",
              "|---|" + "---|" * len(conds)]
    for q in qids:
        cells = []
        for c in conds:
            r = by.get((c, q))
            cells.append(f"{r['weighted_input']:,} / {r['host_seconds']:.0f} / {r['tool_calls']}" if r else "-")
        lines.append(f"| {q} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
