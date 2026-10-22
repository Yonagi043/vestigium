"""Markdown tables from per-session counts and the calibration (numbers only)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

Row = dict[str, Any]


def _md(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _k(n: float | None) -> str:
    return "—" if n is None else f"{n / 1000:,.1f}k"


def _m(n: float | None) -> str:
    return "—" if n is None else f"{n / 1_000_000:,.2f}M"


def session_table(rows: Sequence[Row]) -> str:
    subs = defaultdict(list)
    for r in rows:
        if r["kind"] == "subagent":
            subs[r["parent_tool_use"]].append(r)
    out = []
    for r in rows:
        if r["kind"] != "main" or not r["turns"]:
            continue
        t, tools = r["own_tokens"], r["tools"]
        mine = [x for c in r["subagent_calls"] for x in subs.get(c, [])]
        out.append([
            r["session"][:8], (r["start"] or "")[:16].replace("T", " "), f"{(r['wall_seconds'] or 0) / 60:,.0f}",
            r["turns"], r["copied_turns"], r["prompts"], _k(r["tokens"]["first_prompt"]), _k(t["peak_prompt"]),
            _m(t["prompt_total"]),
            f"{t['cache_read'] / t['prompt_total']:.1%}" if t["prompt_total"] else "—",
            _m(t["weighted_input"]), _k(t["output"]), tools["calls"], sum(v["calls"] for v in tools["mcp"].values()),
            tools["images"], len(mine), _m(sum(x["own_tokens"]["prompt_total"] for x in mine)), len(r["compactions"]),
        ])
    return _md(["会话", "开始(UTC)", "墙钟分钟", "轮数", "其中复制", "用户提示", "首轮提示", "峰值提示", "提示合计", "缓存读占比",
                "加权输入", "输出", "工具调用", "MCP 调用", "图片", "子代理", "子代理提示合计", "压缩次数"], out)


def tool_table(rows: Sequence[Row], top: int = 20) -> str:
    calls: Counter = Counter()
    for r in rows:
        calls.update(r["tools"]["native"])
    return _md(["原生工具", "调用次数"], [[n, c] for n, c in calls.most_common(top)])


def mcp_table(rows: Sequence[Row]) -> str:
    agg: dict[str, Counter] = defaultdict(Counter)
    sessions: dict[str, set] = defaultdict(set)
    for r in rows:
        for name, v in r["tools"]["mcp"].items():
            agg[name].update(v)
            sessions[name].add(r["session"])
    out = [[name, len(sessions[name]), v["calls"], v["result_tokens"], round(v["result_tokens"] / v["calls"]),
            v["images"], v["errors"]] for name, v in sorted(agg.items())]
    return _md(["MCP 工具", "会话数", "调用", "返回 token(o200k)", "每次平均", "图片", "出错"], out) if out else "这批日志里没有 MCP 调用。"


def subagent_table(rows: Sequence[Row]) -> str:
    agg: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["kind"] != "main" and r["turns"]:
            a = agg[(r["kind"], r["agent_type"] or "?")]
            a["n"] += 1
            a["turns"] += r["turns"]
            a["prompt_total"] += r["own_tokens"]["prompt_total"]
            a["weighted"] += r["own_tokens"]["weighted_input"]
            a["output"] += r["own_tokens"]["output"]
            a["first_prompt"] += r["tokens"]["first_prompt"]
    out = [[kind, k, v["n"], v["turns"], _m(v["prompt_total"]), _m(v["weighted"]), _k(v["output"]),
            _k(v["first_prompt"] / v["n"])] for (kind, k), v in sorted(agg.items())]
    return (_md(["来源", "代理类型", "个数", "轮数合计", "提示合计", "加权输入", "输出合计", "首轮提示平均"], out)
            if out else "无")


def report_markdown(rows: Sequence[Row], calib: dict[str, Any]) -> str:
    mains = [r for r in rows if r["kind"] == "main" and r["turns"]]
    total = lambda rs, k: sum(r["own_tokens"][k] for r in rs)  # noqa: E731
    subs = [r for r in rows if r["kind"] != "main"]
    return "\n\n".join([
        "# Claude Code 会话日志计数（自动生成，只含计数）",
        f"主会话 {len(mains)} 个，子代理和工作流代理日志 {len(subs)} 个。“提示”= input + cache 创建 + cache 读取（每次 API "
        "调用实际送入的上下文）；“加权输入”= input + 1.25×cache 创建 + 0.1×cache 读取（按输入价的相对权重）。"
        "返回大小按 o200k 估算。子代理的 token 记在自己的日志里，不计入主会话合计。"
        "续接或分叉的会话会把之前的调用复制进新文件（message id 相同），这些“复制”的轮只算一次，归最早的会话。",
        f"主会话提示合计 {_m(total(mains, 'prompt_total'))}，加权输入 {_m(total(mains, 'weighted_input'))}，"
        f"输出 {_m(total(mains, 'output'))}；子代理和工作流代理提示合计 {_m(total(subs, 'prompt_total'))}，"
        f"加权输入 {_m(total(subs, 'weighted_input'))}。",
        "## 每个主会话", session_table(rows),
        "## 原生工具调用（主会话和子代理合计）", tool_table(rows),
        "## MCP 工具（按工具名汇总）", mcp_table(rows),
        "## 子代理和工作流代理", subagent_table(rows),
        "## 标定（详见 calibration.json）", "```json\n" + json.dumps(calib, ensure_ascii=False, indent=1) + "\n```",
    ]) + "\n"
