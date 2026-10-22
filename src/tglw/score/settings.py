"""Settings of the evaluation tools, for a fresh install and for the research checkout.

Each setting comes from its environment variable, else from the research checkout's defaults
(tglw.common._research_defaults, which is not released), else from a default that names nothing
internal:
- TGLW_EVAL_PYTHON: the interpreter that runs condition T's MCP server (default: this interpreter);
- TGLW_EVAL_CLAUDE: the Claude Code command (default: claude);
- TGLW_EVAL_GUARD: comma-separated fragments that flag a run's tool call when it names one of them
  (question files, answers, other runs' records; default: none beyond the built-in checks);
- TGLW_EVAL_HELDOUT: comma-separated directory-name prefixes that mark held-out material (default:
  heldout). A question file inside such a directory is read only when explicitly allowed; a stage or
  records directory inside one is a held-out stage, whose runs and records must both sit inside such
  directories and whose sessions need a Claude config directory named .claude-<prefix>*.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from tglw.common import _research_defaults as _research
except ImportError:
    _research = None


def _list(name: str, research: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is not None:
        return tuple(x.strip() for x in value.split(",") if x.strip())
    return tuple(getattr(_research, research, default))


PYTHON = os.environ.get("TGLW_EVAL_PYTHON") or getattr(_research, "EVAL_PYTHON", None) or sys.executable
CLAUDE = os.environ.get("TGLW_EVAL_CLAUDE") or "claude"
GUARD = _list("TGLW_EVAL_GUARD", "EVAL_GUARD", ())
HELDOUT = _list("TGLW_EVAL_HELDOUT", "EVAL_HELDOUT", ("heldout",))


def heldout_marker(path: Path) -> str | None:
    """The held-out prefix a path sits under (its first matching directory part), or None."""
    for part in Path(path).parts:
        for m in HELDOUT:
            if part.startswith(m):
                return m
    return None
