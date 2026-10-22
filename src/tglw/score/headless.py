"""Unattended runs of a staged plan (`python -m tglw.score headless`): `claude -p` in each run
directory, strictly one at a time.

Each run gets the launch scripts' environment (IDE hooks removed, CLAUDE_CODE_AUTO_CONNECT_IDE=false,
and also the variables a parent Claude Code session sets for its shells), the condition's pinned
options, the prompt file as its prompt, stream-json output and a turn limit. Nobody answers
permission prompts in headless mode, so a call that would prompt is refused, which is what the
protocol asks of the operator: T allows its six `tgl` tools explicitly; P and W read inside the run
directory, which Claude Code allows without a prompt, and anything else is refused.

stdout is saved as it arrives and watched: an API retry (Claude Code retries 429, 403, 5xx and
network errors), a rate-limit warning or an "API Error" message kills the run and stops the batch.
After a run the batch also stops on an error result, a crash, a missing session log, a permission
denial, API trouble on stderr, or a session whose tools or MCP servers differ from the condition,
so that a person looks first. A run over the timeout is killed and recorded as failed; nothing is rerun automatically.

Records of a run (`<records>/<run_id>/`): stdout.jsonl and stderr.txt as received; session.jsonl and
session/ (the session log, found by its id under ~/.claude/projects and checked against the run
directory, with its tool results and subagent logs); answer.md, audit.txt, measure/, meta.json.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tglw import COMMAND

from . import answer, audit, plan, settings

TGL_TOOLS = ("list_documents", "outline", "read", "context", "search", "region")
SHELL_TOOLS = ("Read", "Grep", "Glob")
FLAGS = {
    "T": ["--strict-mcp-config", "--mcp-config", ".mcp.json", "--tools", "",
          "--allowedTools", ",".join(f"mcp__tgl__{t}" for t in TGL_TOOLS)],
    "P": ["--strict-mcp-config", "--tools", ",".join(SHELL_TOOLS)],
    "W": ["--strict-mcp-config", "--tools", ",".join(SHELL_TOOLS)],
    "V": ["--strict-mcp-config", "--tools", ",".join(SHELL_TOOLS)],
    "S": ["--strict-mcp-config", "--tools", ",".join(SHELL_TOOLS)],
}
# The launch scripts' list, plus what a parent Claude Code session exports to its shells (the
# runner may be started from one) and PYTHONPATH (T's server sets its own in .mcp.json).
CLEAR = re.compile(r"CLAUDE_CODE_\w*|CLAUDECODE|CLAUDE_BRIDGE_\w*|VSCODE_\w*|TERM_PROGRAM|TERM_PROGRAM_VERSION|"
                   r"FORCE_CODE_TERMINAL|GIT_ASKPASS|CLAUDE_EFFORT|CLAUDE_PID|AI_AGENT|TRACEPARENT|PYTHONPATH")
API_TROUBLE = re.compile(r"API Error|rate.?limit|\b429\b|\b403\b|quota|abuse|forbidden|overloaded", re.I)
KILL_GRACE = 10
NOTE = ("headless", "", "", "", "note", f"从这一行起是 headless 模式：{COMMAND} eval headless 自动运行"
        "（claude -p，stream-json），严格串行，两次之间至少停 --pause 秒")
SRC = Path(__file__).resolve().parents[2]


def command(condition: str, prompt_text: str, max_turns: int, claude: str = "claude",
            model: str | None = None) -> list[str]:
    """`model` is passed as --model; use a full model id, since the user's settings may map the
    short aliases (opus, sonnet, haiku) to other models."""
    return [claude, "-p", prompt_text, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "default", *FLAGS[condition], "--max-turns", str(max_turns),
            *(["--model", model] if model else [])]


def clean_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    cleared = sorted(k for k in env if CLEAR.fullmatch(k))
    out = {k: v for k, v in env.items() if k not in cleared}
    out["CLAUDE_CODE_AUTO_CONNECT_IDE"] = "false"
    return out, cleared


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def trouble(msg: dict) -> str:
    """Why a stream-json message means API trouble, or ""."""
    kind = msg.get("type")
    if kind == "system" and msg.get("subtype") == "api_retry":
        return f"API retry (status {msg.get('error_status')}, {msg.get('error')})"
    if kind == "rate_limit_event":
        status = (msg.get("rate_limit_info") or {}).get("status")
        if status != "allowed":
            return f"rate limit event ({status})"
    if kind == "assistant" and (msg.get("message") or {}).get("model") == "<synthetic>":
        for c in msg["message"].get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").startswith("API Error"):
                return c["text"][:200]
    return ""


@dataclass
class Outcome:
    start: str = ""
    end: str = ""
    seconds: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    stop: str = ""
    init: dict | None = None
    result: dict | None = None
    retries: list[dict] = field(default_factory=list)


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def execute(argv: Sequence[str], cwd: Path, env: dict[str, str], rec: Path, timeout: float) -> Outcome:
    """Run one session; stdout goes to rec/stdout.jsonl line by line while it is watched."""
    o = Outcome(start=_iso())
    t0 = time.monotonic()
    with open(rec / "stdout.jsonl", "wb") as so, open(rec / "stderr.txt", "wb") as se:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=se, start_new_session=True)

        def watch() -> None:
            for raw in proc.stdout:
                so.write(raw)
                so.flush()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "system" and msg.get("subtype") == "init" and o.init is None:
                    o.init = msg
                elif msg.get("type") == "system" and msg.get("subtype") == "api_retry":
                    o.retries.append({k: msg.get(k) for k in ("attempt", "error_status", "error")})
                elif msg.get("type") == "result":
                    o.result = msg
                why = trouble(msg)
                if why and not o.stop:
                    o.stop = why

        reader = threading.Thread(target=watch, daemon=True)
        reader.start()
        deadline, killed = t0 + timeout, None
        try:
            while proc.poll() is None:
                now = time.monotonic()
                if killed is None and (o.stop or now > deadline):
                    o.timed_out = not o.stop
                    _signal(proc, signal.SIGTERM)
                    killed = now
                elif killed is not None and now - killed > KILL_GRACE:
                    _signal(proc, signal.SIGKILL)
                time.sleep(0.2)
        finally:
            _signal(proc, signal.SIGKILL)
            proc.wait()
            reader.join(timeout=30)
    o.exit_code = proc.returncode
    o.seconds = round(time.monotonic() - t0, 1)
    o.end = _iso()
    return o


def _records(path: Path) -> list[dict]:
    out = []
    with open(path) as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def find_session(sid: str, run: Path, projects: Path, since: float = 0.0) -> Path | None:
    """The session log with this id (or, without an id, the one newly written for the run
    directory) whose records all carry the run directory as cwd."""
    found = sorted(projects.glob(f"*/{sid}.jsonl")) if sid else [
        p for p in (projects / re.sub(r"[^A-Za-z0-9]", "-", str(run))).glob("*.jsonl")
        if p.stat().st_mtime >= since]
    good = [p for p in found if {r["cwd"] for r in _records(p) if "cwd" in r} == {str(run)}]
    return good[0] if len(good) == 1 else None


def check_init(condition: str, init: dict | None) -> list[str]:
    """Differences between the tools and MCP servers the session reported and the condition."""
    if init is None:
        return ["no init message"]
    tools = set(init.get("tools") or [])
    servers = {(s.get("name"), s.get("status")) for s in init.get("mcp_servers") or []}
    problems = []
    if condition == "T":
        if servers != {("tgl", "connected")}:
            problems.append(f"MCP servers {sorted(servers)}")
        want = {f"mcp__tgl__{t}" for t in TGL_TOOLS}
        if tools - want - audit.T_NATIVE_OK or want - tools:
            problems.append(f"tools {sorted(tools)}")
    else:
        if servers:
            problems.append(f"MCP servers {sorted(servers)}")
        if tools != set(SHELL_TOOLS):
            problems.append(f"tools {sorted(tools)}")
    return problems


def log_settings(session: Path) -> dict[str, list[str]]:
    """Models, effort and Claude Code versions of the main thread's replies, and the model id the
    session announced (its "model" attachment, e.g. claude-opus-5-5[1m] for the 1M-token context)."""
    model, effort, version, model_id = set(), set(), set(), set()
    for r in _records(session):
        a = r.get("attachment")
        if isinstance(a, dict) and a.get("type") == "model" and not r.get("isSidechain"):
            model_id.add(str((a.get("identity") or {}).get("modelId")))
        if r.get("type") != "assistant" or r.get("isSidechain"):
            continue
        m = (r.get("message") or {}).get("model")
        if m and m != "<synthetic>":
            model.add(m)
            effort.update(str(r[k]) for k in ("effort", "perTurnEffort") if r.get(k))
            if r.get("version"):
                version.add(r["version"])
    return {"model": sorted(model), "model_id": sorted(model_id), "effort": sorted(effort),
            "claude_version": sorted(version)}


def measure(session: Path, out: Path) -> dict:
    """Run tglw.measure on the copied log; the main session's figures as in the manual meta.json."""
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-m", "tglw.measure", str(session), "--out", str(out)], env=env,
                          capture_output=True, text=True)
    if done.returncode:
        return {"error": done.stderr.strip()[-500:]}
    rows = [json.loads(line) for line in (out / "sessions.jsonl").read_text().splitlines() if line.strip()]
    main = next((r for r in rows if r["kind"] == "main"), None)
    if main is None:
        return {"error": "no main session"}
    t, tools = main["tokens"], main["tools"]
    return {"turns": main["turns"], "human_prompts": main["prompts"], "wall_seconds": main["wall_seconds"],
            "turn_duration_seconds": main["turn_duration_seconds"], "prompt_total": t["prompt_total"],
            "peak_prompt": t["peak_prompt"], "first_prompt": t["first_prompt"], "output": t["output"],
            "weighted_input": t["weighted_input"], "cache_read": t["cache_read"], "tool_calls": tools["calls"],
            "native": tools["native"], "mcp": tools["mcp"], "tool_errors": tools["errors"],
            "images": tools["images"], "subagent_calls": len(main["subagent_calls"])}


def pdftoppm_version() -> str:
    try:
        done = subprocess.run(["pdftoppm", "-v"], capture_output=True, text=True)
    except FileNotFoundError:
        return ""
    text = (done.stderr or done.stdout).strip()
    return text.splitlines()[0] if text else ""


def preflight(dest: Path, records: Path, row: dict[str, str], rid: str, ledger: Sequence[dict[str, str]]) -> list[str]:
    run = dest / "runs" / rid
    cond = row["condition"]
    want = set(plan.run_files(row))
    problems = []
    if not run.is_dir():
        problems.append(f"{run} is not staged")
    elif {p.name for p in run.iterdir()} != want:
        problems.append(f"{run} holds {sorted(p.name for p in run.iterdir())}, expected only {sorted(want)}")
    elif cond == "T":
        problems += plan.check_allowlist(run / ".mcp.json", plan.row_docs(row))
    if not (dest / "prompts" / f"{rid}.txt").is_file():
        problems.append(f"no prompt {dest / 'prompts' / f'{rid}.txt'}")
    if (records / rid).exists():
        problems.append(f"{records / rid} exists")
    if any(r["run_id"] == rid for r in ledger):
        problems.append(f"{rid} is already in {records / 'ledger.csv'}")
    if cond == "P" and not pdftoppm_version():
        problems.append("pdftoppm is not installed (P reads PDF pages through it)")
    return problems


def append_ledger(records: Path, row: Sequence[str]) -> None:
    path = records / "ledger.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(("run_id", "condition", "question_id", "attempt", "status", "reason"))
        w.writerow(row)


def settle(o: Outcome, timeout: float, init_problems: list[str], session: Path | None, ran: int,
           text: str, warning: str, denials: list, stop_on_denials: bool = True) -> tuple[str, str, str]:
    """(status, reason, why the batch stops or "")."""
    res = o.result or {}
    if o.stop:
        return "failed", f"headless; {o.stop}", o.stop
    if init_problems:
        why = "session differs from the condition: " + "; ".join(init_problems)
        return "invalid", f"headless; {why}", why
    if o.timed_out:
        return "failed", f"headless; timeout after {timeout:g} s (killed)", ""
    if not res:
        return "failed", f"headless; no result (exit code {o.exit_code})", "Claude Code ended without a result"
    if res.get("is_error") and res.get("subtype") != "error_max_turns":
        why = f"error result {res.get('subtype')}: {str(res.get('errors') or res.get('result') or '')[:200]}"
        return "failed", f"headless; {why}", why
    if session is None:
        return "failed", "headless; session log not found", "session log not found"
    if res.get("subtype") == "error_max_turns":
        return "failed", f"headless; max turns reached ({res.get('num_turns')})", ""
    if not text.strip():
        return "failed", "headless; no answer text", ""
    if ran:
        return "invalid", f"headless; audit: {ran} out-of-condition calls ran", ""
    notes = ["headless"] + ([f"answer: {warning}"] if warning else [])
    if denials:
        notes.append(f"{len(denials)} permission denials (refused)")
        stop = f"{len(denials)} permission denials; look before going on" if stop_on_denials else ""
        return "valid", "; ".join(notes), stop
    return "valid", "; ".join(notes), ""


def run_one(dest: Path, records: Path, row: dict[str, str], rid: str, *, timeout: float, max_turns: int,
            claude: str, projects: Path, stage: str, config_dir: Path | None = None,
            model: str | None = None, stop_on_denials: bool = True) -> tuple[str, str, str]:
    cond, qid, attempt = plan.parse_run_id(rid)
    run = (dest / "runs" / rid).resolve()
    rec = records / rid
    rec.mkdir(parents=True)
    prompt_file = dest / "prompts" / f"{rid}.txt"
    argv = command(cond, prompt_file.read_text(), max_turns, claude, model)
    env, cleared = clean_env(dict(os.environ))
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    o = execute(argv, run, env, rec, timeout)

    sid = (o.init or o.result or {}).get("session_id", "")
    found = find_session(sid, run, projects, since=time.time() - o.seconds - 5)
    session = None
    if found:
        session = rec / "session.jsonl"
        shutil.copy2(found, session)
        if found.with_suffix("").is_dir():
            shutil.copytree(found.with_suffix(""), rec / "session")
    text, warning = "", ""
    if session:
        try:
            got = answer.final_answer(session)
            text, warning = got.text, got.warning
        except ValueError as e:
            warning = str(e)
    if text:
        (rec / "answer.md").write_text(text)
    ran, refused, lines = 0, 0, ["no session log"]
    if session:
        lines, ran = audit.report(session, cond, run, plan.row_docs(row))
        refused = sum(c.status == "refused" for c in audit.tool_calls(session))
    (rec / "audit.txt").write_text("\n".join(lines) + "\n")
    res = o.result or {}
    denials = res.get("permission_denials") or []
    init_problems = check_init(cond, o.init) if o.init or res else []
    status, reason, stop = settle(o, timeout, init_problems, session, ran, text, warning, denials, stop_on_denials)
    err = next((line for line in (rec / "stderr.txt").read_text(errors="replace").splitlines()
                if API_TROUBLE.search(line)), "")
    if err and not stop:
        stop = f"stderr: {err[:200]}"

    meta = {
        "run_id": rid, "stage": stage, "condition": cond, "question_id": qid, "attempt": attempt,
        "status": status, "reason": reason, "mode": "headless", "stopped_batch": stop or None,
        "session_id": [sid] if sid else [], "cwd": [str(run)], "start": o.start, "end": o.end,
        "host_seconds": o.seconds, "exit_code": o.exit_code, "timed_out": o.timed_out,
        **(log_settings(session) if session else {"model": [], "model_id": [], "effort": [], "claude_version": []}),
        "init": {k: (o.init or {}).get(k) for k in ("model", "permissionMode", "tools", "mcp_servers",
                                                    "claude_code_version")},
        "init_problems": init_problems,
        "launch": {"argv": [a if a != argv[2] else f"<prompt: {prompt_file}>" for a in argv],
                   "claude": shutil.which(claude) or claude, "cwd": str(run), "cleared": cleared,
                   "config_dir": str(config_dir) if config_dir else None,
                   "set": {"CLAUDE_CODE_AUTO_CONNECT_IDE": "false"}, "timeout_seconds": timeout,
                   "max_turns": max_turns,
                   "model_settings": {v: env[v] for v in plan.SETTINGS_VARS if v in env}},
        "result": {k: res.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms",
                                           "total_cost_usd", "stop_reason", "terminal_reason", "usage", "modelUsage")},
        "api_retries": o.retries,
        "permission_denials": [{"tool_name": d.get("tool_name"), "tool_input": d.get("tool_input")} for d in denials],
        "refused_calls": refused,
        "audit": lines[-1],
        "answer": {"chars": len(text), "warning": warning,
                   "same_as_result": text.strip() == str(res.get("result") or "").strip() if res else None},
        "pdftoppm": pdftoppm_version() if cond == "P" else None,
        "measure": measure(session, rec / "measure") if session else {},
        "recorded_by": "tglw.score headless",
    }
    (rec / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    append_ledger(records, (rid, cond, qid, str(attempt), status, reason))
    return status, reason, stop


def _queue(rows: Sequence[dict[str, str]], ledger: list[dict[str, str]], only: str | None) -> tuple[list[tuple[str, int]], str]:
    """Runs to do now, in order, and a note on what is left for a person."""
    if only:
        cond, qid, _ = plan.parse_run_id(only)
        pos = next((i for i, r in enumerate(rows, 1) if (r["condition"], r["question_id"]) == (cond, qid)), 0)
        return ([(only, pos)], "") if pos else ([], f"{only} is not in the plan")
    out: list[tuple[str, int]] = []
    seen = list(ledger)
    while True:
        nxt = plan.next_run(rows, seen)
        if nxt is None:
            return out, ""
        rid, kind, pos = nxt
        if kind != "first attempt":
            return out, f"left for a person: {rid} ({kind}); failed runs are not rerun automatically"
        out.append((rid, pos))
        cond, qid, attempt = plan.parse_run_id(rid)
        seen.append({"run_id": rid, "condition": cond, "question_id": qid, "attempt": str(attempt), "status": "valid"})


def dry_line(dest: Path, records: Path, rid: str, max_turns: int, timeout: float, claude: str,
             config_dir: Path | None = None, model: str | None = None) -> str:
    cond = plan.parse_run_id(rid)[0]
    prompt_file = dest / "prompts" / f"{rid}.txt"
    argv = command(cond, "\0", max_turns, claude, model)
    shown = " ".join(f'"$(cat {shlex.quote(str(prompt_file))})"' if a == "\0" else shlex.quote(a) for a in argv)
    _, cleared = clean_env(dict(os.environ))
    unset = " ".join(f"-u {v}" for v in cleared)
    config = f"CLAUDE_CONFIG_DIR={shlex.quote(str(config_dir))} " if config_dir else ""
    return (f"cd {shlex.quote(str(dest / 'runs' / rid))}\n"
            f"env {unset} {config}CLAUDE_CODE_AUTO_CONNECT_IDE=false {shown} > {records / rid / 'stdout.jsonl'}"
            f"    # killed after {timeout:g} s")


def run_batch(dest: Path, plan_file: Path, records: Path, *, only: str | None = None, pause: float = 60,
              timeout: float = 900, max_turns: int = 60, dry_run: bool = False, claude: str = "claude",
              projects: Path | None = None, allow_frozen: bool = False, config_dir: Path | None = None,
              model: str | None = None, stop_on_denials: bool = True, out: Callable[[str], None] = print,
              sleep: Callable[[float], None] = time.sleep) -> int:
    """0: done; 1: nothing run (refused before starting); 2: the batch stopped for a person.
    `config_dir` becomes the sessions' CLAUDE_CONFIG_DIR (default: the variable's current value);
    held-out questions need one other than ~/.claude, so their prompts and logs stay out of it. A
    held-out stage (stage or records inside a directory whose name starts with a settings.HELDOUT
    prefix) is held to the same rules whatever its questions, keeps both its runs and its records
    under such directories, and needs a config directory named .claude-<prefix>*."""
    if config_dir is None and os.environ.get("CLAUDE_CONFIG_DIR"):
        config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    projects = projects or (config_dir or Path.home() / ".claude") / "projects"
    rows = plan.read_plan(plan_file)
    by_key = {(r["condition"], r["question_id"]): r for r in rows}
    marker = settings.heldout_marker(dest.resolve()) or settings.heldout_marker(records.resolve())
    if marker or any(r.get("split") == "frozen" for r in rows):
        what = f"{dest} is a held-out stage" if marker else f"{plan_file} has held-out questions"
        if not allow_frozen:
            out(f"{what}; refusing without --allow-heldout")
            return 1
        if config_dir is None or config_dir.resolve() == (Path.home() / ".claude").resolve():
            out(f"{what}; refusing without --config-dir other than ~/.claude")
            return 1
        if marker and not (settings.heldout_marker(dest.resolve()) and settings.heldout_marker(records.resolve())):
            out(f"{what}; its runs and records must both be inside held-out directories (records: {records})")
            return 1
        if marker and not config_dir.name.startswith(f".claude-{marker}"):
            out(f"{what}; refusing a --config-dir not named .claude-{marker}* ({config_dir})")
            return 1
    queue, left = _queue(rows, plan.ledger_rows(records), only)
    if not queue:
        out(left or f"nothing to run: every run of {plan_file} is settled in {records / 'ledger.csv'}")
        return 1 if only else 0
    if dry_run:
        for n, (rid, pos) in enumerate(queue, 1):
            if n > 1:
                out(f"sleep {pause:g}")
            out(f"# {n}/{len(queue)}  {rid}  (plan row {pos})")
            out(dry_line(dest, records, rid, max_turns, timeout, claude, config_dir, model))
        if left:
            out(f"# {left}")
        return 0
    records.mkdir(parents=True, exist_ok=True)
    lock = records / "headless.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        out(f"{lock} exists: another runner is working on these records (or one was killed; check, then delete it)")
        return 1
    os.write(fd, f"{os.getpid()} {_iso()}\n".encode())
    os.close(fd)
    last = records / "headless_last_end"
    try:
        for n, (rid, pos) in enumerate(queue, 1):
            cond, qid, _ = plan.parse_run_id(rid)
            ledger = plan.ledger_rows(records)
            problems = preflight(dest, records, by_key[(cond, qid)], rid, ledger)
            if problems:
                out(f"{rid}: not started: " + "; ".join(problems))
                return 1 if n == 1 else 2
            if last.exists():
                wait = float(last.read_text().split()[0]) + pause - time.time()
                if wait > 0:
                    out(f"pause {wait:.0f} s")
                    sleep(wait)
            if not any(r["status"] == "note" and r["run_id"] == NOTE[0] for r in ledger):
                append_ledger(records, NOTE)
            out(f"[{n}/{len(queue)}] {rid} (plan row {pos}) started {_iso()}")
            status, reason, stop = run_one(dest, records, by_key[(cond, qid)], rid, timeout=timeout,
                                           max_turns=max_turns, claude=claude, projects=projects,
                                           stage=dest.name, config_dir=config_dir, model=model,
                                           stop_on_denials=stop_on_denials)
            last.write_text(f"{time.time()} {rid}\n")
            out(f"[{n}/{len(queue)}] {rid}: {status} ({reason})")
            if stop:
                out(f"batch stopped after {rid}: {stop}")
                return 2
        if left:
            out(left)
        return 0
    finally:
        lock.unlink(missing_ok=True)
