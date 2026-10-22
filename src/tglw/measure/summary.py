"""Per-session counts (no text): turns, tokens, wall clock, tool calls split native vs MCP, subagents."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .log import SUBAGENT_TOOLS, Session

# Relative price weights for Claude input classes (input = 1): 5-minute cache write 1.25x,
# cache read 0.1x. Output is reported separately.
CACHE_WRITE = 1.25
CACHE_READ = 0.1
DROP = 0.7


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return round((parse(end) - parse(start)).total_seconds(), 1)


def compactions(s: Session) -> list[int]:
    """Turns whose prompt shrank below DROP x the previous one: context was compacted or reset."""
    return [t.index for prev, t in zip(s.turns, s.turns[1:]) if t.prompt < DROP * prev.prompt]


def _mcp_table(s: Session) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for c in s.calls.values():
        if c.mcp:
            row = out.setdefault(c.name, {"calls": 0, "result_chars": 0, "result_tokens": 0, "images": 0, "errors": 0})
            row["calls"] += 1
            row["result_chars"] += c.result_chars
            row["result_tokens"] += c.result_tokens
            row["images"] += c.images
            row["errors"] += c.error
    return out


def _tokens(t: list) -> dict[str, int]:
    tokens = {
        "input": sum(x.input for x in t),
        "cache_creation": sum(x.cache_creation for x in t),
        "cache_read": sum(x.cache_read for x in t),
        "output": sum(x.output for x in t),
        "prompt_total": sum(x.prompt for x in t),
        "peak_prompt": max((x.prompt for x in t), default=0),
        "first_prompt": t[0].prompt if t else 0,
        "last_prompt": t[-1].prompt if t else 0,
    }
    tokens["weighted_input"] = round(tokens["input"] + CACHE_WRITE * tokens["cache_creation"]
                                     + CACHE_READ * tokens["cache_read"])
    return tokens


def summarize(s: Session) -> dict[str, Any]:
    t = s.turns
    calls = list(s.calls.values())
    own = [x for x in t if x.index not in s.copied]
    return {
        "session": s.session_id,
        "kind": s.kind,
        "agent_type": s.agent_type,
        "parent_tool_use": s.parent_tool_use,
        "file": str(s.path),
        "turns": len(t),
        "copied_turns": len(t) - len(own),
        "prompts": s.prompts,
        "start": s.first_ts,
        "end": s.last_ts,
        "wall_seconds": _seconds(s.first_ts, s.last_ts),
        "turn_duration_seconds": round(sum(s.turn_durations_ms) / 1000, 1),
        "tokens": _tokens(t),
        "own_tokens": _tokens(own),
        "tools": {
            "calls": len(calls),
            "native": dict(Counter(c.name for c in calls if not c.mcp)),
            "mcp": _mcp_table(s),
            "result_chars": sum(c.result_chars for c in calls),
            "result_tokens": sum(c.result_tokens for c in calls),
            "images": sum(c.images for c in calls),
            "errors": sum(c.error for c in calls),
            "unreturned": sum(not c.returned for c in calls),
        },
        "subagent_calls": [c.id for c in calls if c.name in SUBAGENT_TOOLS],
        "attachments": dict(s.attachments),
        "snapshot_tokens": s.snapshot_tokens,
        "compactions": compactions(s),
        "bad_lines": s.bad_lines,
        "synthetic": s.synthetic,
    }


def call_rows(s: Session) -> list[dict[str, Any]]:
    """One row per tool call: which session and turn, the tool, and what came back (sizes only)."""
    return [{"session": s.session_id, "kind": s.kind, "call": c.id, "tool": c.name, "mcp": c.mcp, "turn": c.turn,
             "result_chars": c.result_chars, "result_tokens": c.result_tokens, "images": c.images,
             "error": c.error, "returned": c.returned} for c in s.calls.values()]
