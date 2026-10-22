"""Final answer text from a Claude Code session log (`~/.claude/projects/<dir>/<session>.jsonl`).

Each assistant reply is stored as one or more lines of type "assistant" sharing `message.id`, one
content block per line (thinking, text, tool_use). The answer is the main-thread (non-sidechain)
text after the last human message; local error messages (model "<synthetic>") are skipped. When the
last text reply is short and earlier replies of the same turn carry more text (a closing line after
the real answer), every text reply of the turn is returned and a warning is set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SHORT = 200


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    warning: str = ""


_LOCAL = ("<command-name>", "<command-message>", "<local-command", "<bash-input>", "<bash-stdout>",
          "<task-notification>")


def _human(rec: dict) -> bool:
    """A message the operator typed; slash commands such as /exit and their local output are not."""
    content = (rec.get("message") or {}).get("content")
    texts = [content] if isinstance(content, str) else [
        c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
    return any(t.strip() and not t.lstrip().startswith(_LOCAL) for t in texts)


def final_answer(path: Path) -> Answer:
    turn: dict[str, list[str]] = {}
    order: list[str] = []
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("isSidechain") or rec.get("isMeta"):
                continue
            if rec.get("type") == "user" and _human(rec):
                turn, order = {}, []
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            if msg.get("model") == "<synthetic>":
                continue
            parts = [c.get("text", "") for c in msg.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip()]
            if not parts:
                continue
            mid = msg.get("id") or rec.get("uuid", "")
            if mid not in turn:
                turn[mid] = []
                order.append(mid)
            turn[mid] += parts
    if not order:
        raise ValueError(f"{path}: no assistant text after the last human message")
    last = "\n\n".join(turn[order[-1]]).strip()
    if len(last) < SHORT and len(order) > 1:
        whole = "\n\n".join("\n\n".join(turn[m]) for m in order).strip()
        return Answer(whole + "\n", f"last reply is {len(last)} characters; all {len(order)} text replies of the turn kept")
    return Answer(last + "\n")


def final_text(path: Path) -> str:
    return final_answer(path).text
