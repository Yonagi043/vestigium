"""Check a run's session log (and its subagent logs) for calls outside the run's condition.

Reports tool names, the matching path fragment and whether the call ran or was refused; never the
message text. Flagged everywhere: web and delegation tools (WebSearch, WebFetch, Agent, Task,
Workflow, Skill), environment dumps (env, printenv), paths outside the run directory (absolute,
`..`, `~`, `$HOME`), and fragments naming the tool's work area, Claude Code's config directory, or
anything listed in settings.GUARD (question files, other runs and their grading, held-out config
directories). A Grep pattern is search text, not a location, and is left out of these checks. T may use only the `tgl` MCP tools (plus
TodoWrite and ToolSearch), and, when the run's documents are given, only on those: a call naming
another document is flagged (P, W and V only have their own documents in the run directory). The
name is resolved as the server resolved it, within its allowlist when the run's .mcp.json sets
TGL_ALLOWED_DOCS, so a shortened name of a run's own document passes; a call the allowlist turned
away is flagged as refused. P, W and V may use the shell's own tools, but not
MCP tools, and every Bash command is listed for a human to read. The run's own store for large tool
outputs (`<config dir>/projects/<the run directory's project>/[<session>/]tool-results/`, under
~/.claude or any other CLAUDE_CONFIG_DIR) is allowed; other sessions' stores are not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tglw.common import paths, registry

from . import settings
from .plan import ALLOWED_DOCS_ENV

ALWAYS = {"WebSearch", "WebFetch", "Agent", "Task", "Workflow", "Skill"}
T_NATIVE_OK = {"TodoWrite", "ToolSearch"}
# the work area (and the research copy of the PDFs and caches), then the guard list with Claude Code's
# config directory placed before the guard's own config-directory fragments
_ROOTS = (f"/{paths.WORK.name}",) + ((f"/{paths.TGL.name}/",) if paths.TGL else ())
FORBIDDEN = (_ROOTS + tuple(g for g in settings.GUARD if not g.startswith("/.claude")) + ("/.claude/",)
             + tuple(g for g in settings.GUARD if g.startswith("/.claude")))
_ABS = re.compile(r"(?<![\w.~$*-])/(?:[\w.@+-]+/)*[\w.@+-]*")  # not the "/" of a glob such as **/*.pdf
_ESCAPE = re.compile(r"(?:^|[\s'\"=(:])(?:\.\.(?:/|$|[\"'\s])|~|\$\{?HOME\b)|\bcd\s+[/~.$]|\b(?:find|ls|du|tree)\s+/")
_ENV = re.compile(r"(?:^|[;&|\s])(?:env|printenv|set|export)(?:\s|$|;)")
_DENIED = ("doesn't want to proceed", "denied", "rejected", "not allowed", "permission",
           "this session can only use")  # the last: the tgl server refusing a document outside its allowlist
_PROJECT = re.compile(r"[^A-Za-z0-9]")


def _own_store(run: str) -> re.Pattern:
    """Claude Code's store for this run's large tool outputs: projects/<run dir with every
    non-alphanumeric character as "-">/[<session>/]tool-results/, in whatever config directory."""
    return re.compile(r"/[^\s\"']*/projects/" + re.escape(_PROJECT.sub("-", run)) + r"/(?:[^/\s\"']+/)?tool-results/")


@dataclass(frozen=True, slots=True)
class Call:
    index: int
    log: str
    tool: str
    args: str
    status: str


@dataclass(frozen=True, slots=True)
class Finding:
    index: int
    tool: str
    reason: str
    status: str = "ran"


def session_logs(path: Path) -> list[Path]:
    """The main log plus subagent logs in `<stem>/subagents/` (Claude Code's layout) or a copied
    `session/` directory next to it."""
    logs = [path]
    for d in (path.with_suffix(""), path.parent / "session"):
        sub = d / "subagents"
        if sub.is_dir():
            logs += sorted(sub.rglob("*.jsonl"))
    return logs


def _result_status(content: object, is_error: bool) -> str:
    text = json.dumps(content, ensure_ascii=False).lower() if not isinstance(content, str) else content.lower()
    if is_error and any(d in text for d in _DENIED):
        return "refused"
    return "error" if is_error else "ran"


def tool_calls(path: Path) -> list[Call]:
    calls: list[tuple[str, str, str, str]] = []
    status: dict[str, str] = {}
    for log in session_logs(path):
        with open(log) as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if rec.get("type") == "assistant" and c.get("type") == "tool_use":
                        calls.append((c.get("id", ""), log.name, c.get("name", ""),
                                      json.dumps(c.get("input") or {}, ensure_ascii=False)))
                    elif rec.get("type") == "user" and c.get("type") == "tool_result":
                        status[c.get("tool_use_id", "")] = _result_status(c.get("content"), bool(c.get("is_error")))
    return [Call(i, log, name, args, status.get(cid, "no-result")) for i, (cid, log, name, args) in enumerate(calls, 1)]


def _input(c: Call) -> dict:
    """A call's arguments as a dict; {} when the logged input is not a JSON object."""
    try:
        inp = json.loads(c.args)
    except ValueError:
        return {}
    return inp if isinstance(inp, dict) else {}


def resolve_name(name: str, names: Sequence[str]) -> str | None:
    """The document a `tgl` document argument means among `names` under the server's rule
    (`resolve_doc`: the exact name, or a fragment of exactly one name, ignoring case); None when it
    means none, so the server opened no document."""
    if name in names:
        return name
    key = name.strip().lower()
    hits = [n for n in names if key and key in n.lower()]
    return hits[0] if len(hits) == 1 else None


def registered_name(name: str) -> str | None:
    """`resolve_name` over every registered document."""
    return resolve_name(name, [d.name for d in registry.docs()])


def served_allowlist(run_dir: Path) -> list[str] | None:
    """The documents a T run's server was limited to (TGL_ALLOWED_DOCS in its .mcp.json), or None."""
    try:
        raw = json.loads((run_dir / ".mcp.json").read_text())["mcpServers"]["tgl"]["env"].get(ALLOWED_DOCS_ENV)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return [n.strip() for n in raw.split(",") if n.strip()] if raw else None


def _other_document(doc: str, docs: Sequence[str], served: Sequence[str] | None) -> str | None:
    """The document outside `docs` a `tgl` call opened, or reached for, or None. The name is resolved as
    the server resolved it: within its allowlist if it had one, else among all registered documents.
    When that resolves to nothing the server opened nothing; the call still reached for another
    document if the name means one among all registered documents (an allowlist refusal)."""
    used = resolve_name(doc, served) if served is not None else registered_name(doc)
    if used is not None:
        return None if used in docs else used
    meant = registered_name(doc) if served is not None else None
    return meant if meant is not None and meant not in docs else None


def audit(path: Path, condition: str, run_dir: Path, docs: Sequence[str] = ()) -> list[Finding]:
    run = str(run_dir.resolve())
    own = _own_store(run)
    served = served_allowlist(run_dir) if condition == "T" and docs else None
    found: list[Finding] = []
    for c in tool_calls(path):
        def flag(reason: str) -> None:
            found.append(Finding(c.index, c.tool, reason, c.status))

        checked = c.args
        if c.tool == "Grep" and (inp := _input(c)):
            # the pattern is search text, not a location; the location is `path`
            checked = json.dumps({k: v for k, v in inp.items() if k != "pattern"}, ensure_ascii=False)
        args = own.sub("TOOLRESULTS:", checked.replace(run + "/", "RUNDIR:").replace(run, "RUNDIR"))
        if c.tool in ALWAYS:
            flag("web or delegation tool")
        if condition == "T":
            if not c.tool.startswith("mcp__tgl__") and c.tool not in T_NATIVE_OK and c.tool not in ALWAYS:
                flag("non-MCP tool in condition T")
            elif docs and c.tool.startswith("mcp__tgl__") and (doc := _input(c).get("doc")) is not None \
                    and (other := _other_document(str(doc), docs, served)):
                flag(f"document not named in the prompt: {other}")
        elif c.tool.startswith("mcp__"):
            flag(f"MCP tool in condition {condition}")
        bad = [f for f in FORBIDDEN if f in args]
        if bad:
            flag(f"names {', '.join(bad)}")
        outside = sorted({p for p in _ABS.findall(args) if p != "/dev/null" and not p.startswith("/dev/")})
        if outside:
            flag(f"path outside the run directory: {', '.join(outside[:3])}")
        if _ESCAPE.search(args):
            flag("relative escape (.., ~, $HOME, cd, find /)")
        if c.tool == "Bash" and _ENV.search(_input(c).get("command", c.args)):
            flag("environment dump (env, printenv, set, export)")
    return found


def bash_commands(path: Path) -> list[tuple[int, str, str]]:
    """Every Bash command with its call number and status, for a human to read."""
    return [(c.index, c.status, _input(c).get("command", c.args)) for c in tool_calls(path) if c.tool == "Bash"]


def report(path: Path, condition: str, run_dir: Path, docs: Sequence[str] = ()) -> tuple[list[str], int]:
    """The `audit` command's lines, and how many flagged calls were not refused."""
    findings = audit(path, condition, run_dir, docs)
    lines = [f"flag\tcall {f.index}\t{f.status}\t{f.tool}\t{f.reason}" for f in findings]
    if condition != "T":
        lines += [f"bash\tcall {i}\t{status}\t{cmd[:300]}" for i, status, cmd in bash_commands(path)]
    ran = sum(f.status != "refused" for f in findings)
    lines.append(f"{len(tool_calls(path))} tool calls, {len(findings)} flagged, {ran} of them not refused")
    return lines, ran
