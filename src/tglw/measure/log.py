"""Parse one Claude Code session log (~/.claude/projects/<project>/<session>.jsonl) into counts.

Only sizes and counts are kept; no message text leaves this module.

A log line is one record. An API response is split into one assistant record per content block
(thinking, text, tool_use); they share `message.id` and carry the same usage, so turns are
deduplicated by that id. Synthetic assistant records (local errors, zero usage) are not turns. Tool results come back in user records as `tool_result` blocks whose
content is a string or a list of text/image blocks. Subagents write their own logs under
`<session>/subagents/agent-*.jsonl`, with a `.meta.json` naming the parent's tool_use id.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tglw.common.text import count_tokens

SUBAGENT_TOOLS = ("Agent", "Task")
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


@dataclass(frozen=True, slots=True)
class Turn:
    """One API call: its usage, when it was logged, and the context added since the previous call."""

    index: int
    msg_id: str
    ts: str
    input: int
    cache_creation: int
    cache_read: int
    output: int
    added_tokens: int
    added_images: int

    @property
    def prompt(self) -> int:
        return self.input + self.cache_creation + self.cache_read


@dataclass(slots=True)
class Call:
    id: str
    name: str
    turn: int
    result_chars: int = 0
    result_tokens: int = 0
    images: int = 0
    error: bool = False
    returned: bool = False

    @property
    def mcp(self) -> bool:
        return self.name.startswith("mcp__")


@dataclass
class Session:
    path: Path
    session_id: str
    kind: str
    agent_type: str | None = None
    parent_tool_use: str | None = None
    turns: list[Turn] = field(default_factory=list)
    calls: dict[str, Call] = field(default_factory=dict)
    prompts: int = 0
    attachments: Counter = field(default_factory=Counter)
    first_ts: str | None = None
    last_ts: str | None = None
    turn_durations_ms: list[int] = field(default_factory=list)
    snapshot_tokens: dict[str, int] = field(default_factory=dict)
    bad_lines: int = 0
    synthetic: int = 0
    copied: set[int] = field(default_factory=set)


def records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"type": "<bad line>"}


def _text_size(value: Any) -> tuple[int, int, int]:
    """(chars, o200k tokens, images) of a tool_result or rendered content value."""
    if value is None:
        return 0, 0, 0
    if isinstance(value, str):
        return len(value), count_tokens(value), 0
    chars = tokens = images = 0
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str):
            chars, tokens = chars + len(item), tokens + count_tokens(item)
        elif isinstance(item, dict) and item.get("type") == "image":
            images += 1
        elif isinstance(item, dict):
            c, t, i = _text_size(item.get("text", item.get("content")))
            chars, tokens, images = chars + c, tokens + t, images + i
    return chars, tokens, images


class _Builder:
    def __init__(self, session: Session):
        self.s = session
        self.seen: set[str] = set()
        self.added_tokens = 0
        self.added_images = 0

    def feed(self, r: dict[str, Any]) -> None:
        kind = r.get("type")
        ts = r.get("timestamp")
        if ts:
            self.s.first_ts = self.s.first_ts or ts
            self.s.last_ts = ts
        if kind == "<bad line>":
            self.s.bad_lines += 1
        elif kind == "assistant":
            self._assistant(r)
        elif kind == "user":
            self._user(r)
        elif kind == "attachment":
            self._attachment(r)
        elif kind == "system" and r.get("subtype") == "turn_duration" and r.get("durationMs") is not None:
            self.s.turn_durations_ms.append(int(r["durationMs"]))

    def _assistant(self, r: dict[str, Any]) -> None:
        m = r.get("message") or {}
        mid = m.get("id")
        u = m.get("usage") or {}
        turn = len(self.s.turns) - 1
        if m.get("model") == "<synthetic>" or not any(u.get(k) for k in USAGE_KEYS):
            self.s.synthetic += 1
            return
        if mid and mid not in self.seen:
            self.seen.add(mid)
            turn = len(self.s.turns)
            self.s.turns.append(Turn(
                turn, mid, r.get("timestamp", ""), int(u.get("input_tokens") or 0),
                int(u.get("cache_creation_input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0),
                int(u.get("output_tokens") or 0), self.added_tokens, self.added_images,
            ))
            self.added_tokens = self.added_images = 0
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in self.s.calls:
                self.s.calls[b["id"]] = Call(b["id"], str(b.get("name")), turn)

    def _user(self, r: dict[str, Any]) -> None:
        content = (r.get("message") or {}).get("content")
        if isinstance(content, str):
            if r.get("promptSource") == "typed" or (not r.get("isMeta") and r.get("origin") is None):
                self.s.prompts += 1
            self.added_tokens += count_tokens(content)
            return
        for b in content or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                chars, tokens, images = _text_size(b.get("content"))
                call = self.s.calls.get(b.get("tool_use_id", ""))
                if call is not None:
                    call.result_chars, call.result_tokens, call.images = chars, tokens, images
                    call.error, call.returned = bool(b.get("is_error")), True
                self.added_tokens += tokens
                self.added_images += images
            elif b.get("type") == "text":
                self.added_tokens += count_tokens(b.get("text") or "")
            elif b.get("type") == "image":
                self.added_images += 1

    def _attachment(self, r: dict[str, Any]) -> None:
        a = r.get("attachment") or {}
        self.s.attachments[str(a.get("type"))] += 1
        if a.get("type") == "prompt_snapshot":
            for key in ("systemPrompt", "tools"):
                if a.get(key):
                    value = a[key] if isinstance(a[key], str) else json.dumps(a[key], ensure_ascii=False)
                    self.s.snapshot_tokens[key] = count_tokens(value)
        for item in r.get("rendered") or []:
            _, tokens, images = _text_size(item.get("content") if isinstance(item, dict) else item)
            self.added_tokens += tokens
            self.added_images += images


def parse(path: Path, kind: str = "main") -> Session:
    s = Session(path, path.stem, kind)
    meta = path.with_suffix(".meta.json")
    if kind != "main" and meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        s.agent_type, s.parent_tool_use = m.get("agentType"), m.get("toolUseId")
    b = _Builder(s)
    for r in records(path):
        if r.get("sessionId") and kind == "main":
            s.session_id = r["sessionId"]
        b.feed(r)
    return s


def subagent_logs(main: Path) -> list[tuple[Path, str]]:
    """(log, kind) for agents the session spawned: "subagent" for Agent/Task calls, "workflow"
    for agents a Workflow run spawned (under subagents/workflows/<run>/)."""
    root = main.parent / main.stem / "subagents"
    return [(p, "workflow" if "workflows" in p.relative_to(root).parts else "subagent")
            for p in sorted(root.rglob("agent-*.jsonl"))]


def mark_copies(sessions: list[Session]) -> None:
    """A resumed or forked session repeats earlier API calls (same message ids) in a new file.
    Mark such turns as copies, keeping the earliest-starting session as their owner."""
    seen: set[str] = set()
    for s in sorted(sessions, key=lambda x: (x.first_ts or "", str(x.path))):
        s.copied = {t.index for t in s.turns if t.msg_id in seen}
        seen.update(t.msg_id for t in s.turns)


def discover(target: Path) -> list[Path]:
    """Main session logs under a file or a project directory (subagent logs excluded)."""
    if target.is_file():
        return [target]
    return sorted(p for p in target.glob("*.jsonl") if p.is_file())
