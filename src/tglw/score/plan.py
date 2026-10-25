"""Prompts, run plans, run directories and a frozen server snapshot for the T / P / W / V comparison.

Every condition gets the same prompt; only the line naming the material differs: T reads the
document through the `tgl` MCP server, P gets a copy of the PDF, W a copy of the whole-document
text, V the same kind of text rebuilt with task D1's repairs (task W2, data/W2/fulltext). V's
prompt is W's word for word. Plans default to T, P and W; V is asked for with `conditions`. A plan
lists runs in a seeded random order (questions shuffled, and the order of conditions shuffled within
each question). `stage` lays a stage out as

    <dest>/runs/<run_id>/       the session's working directory: the material and nothing else
    <dest>/prompts/<run_id>.txt the prompt to paste
    <dest>/launch/<run_id>.sh   the launch script: clears IDE hooks from the environment, then
                                starts Claude Code with the pinned options

Records (answers, logs, ledger), sheets and the blinding key belong under a separate root.

A question whose evidence cites other documents gets them all: the plan row lists them in
`other_docs`, P and W/V runs get one copy per document, and the prompt (`PROMPT_MULTI`) names every
document and asks for citations as (<document as named>, p. N). A single-document question keeps
`PROMPT` word for word. T's `.mcp.json` lists the row's documents in TGL_ALLOWED_DOCS, for single-
and cross-document questions alike, so the server lists and opens only those, as P, W and V only have
them in the run directory; `check_allowlist` refuses a T run whose server does not know the setting.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shlex
import shutil
from collections.abc import Sequence
from pathlib import Path

from tglw.common import registry
from tglw.common.paths import DATA
from tglw.zh.chars import has_han

from . import settings
from .questions import Question

CONDITIONS = ("T", "P", "W", "V", "S")
RUN_CONDITIONS = CONDITIONS + ("R", "U")  # staged by their own scripts, not planned from questions
ONLY_TOOL = {"R": "trace", "U": "rules"}  # the one tgl tool of R (trace questions) and U (rule writing)
DEFAULT_CONDITIONS = ("T", "P", "W")
FULLTEXT = {"W": DATA / "K" / "fulltext", "V": DATA / "W2" / "fulltext"}
PLAN_COLUMNS = ("order", "run_id", "stage", "condition", "question_id", "split", "doc", "material", "other_docs")
PYTHON = settings.PYTHON  # runs T's MCP server
ALLOWED_DOCS_ENV = "TGL_ALLOWED_DOCS"  # tglw.mcp_server.tools.ALLOW_ENV: comma-separated documents the server may use
SNAPSHOT_DATA = ("common", "A", "B", "C1", "C2", "D1", "D2", "F", "G", "M", "M2")
LAUNCH = {
    "T": 'claude --permission-mode default --strict-mcp-config --mcp-config .mcp.json --tools ""',
    "P": 'claude --permission-mode default --strict-mcp-config --tools "Read,Grep,Glob"',
    "W": 'claude --permission-mode default --strict-mcp-config --tools "Read,Grep,Glob"',
    "V": 'claude --permission-mode default --strict-mcp-config --tools "Read,Grep,Glob"',
    "S": 'claude --permission-mode default --strict-mcp-config --tools "Read,Grep,Glob"',
    "R": 'claude --permission-mode default --strict-mcp-config --mcp-config .mcp.json --tools "" '
         '--allowedTools mcp__tgl__trace --disallowedTools '
         'mcp__tgl__list_documents,mcp__tgl__outline,mcp__tgl__read,mcp__tgl__context,mcp__tgl__search,mcp__tgl__region',
    "U": 'claude --permission-mode default --strict-mcp-config --mcp-config .mcp.json --tools "" '
         '--allowedTools mcp__tgl__rules --disallowedTools '
         'mcp__tgl__list_documents,mcp__tgl__outline,mcp__tgl__read,mcp__tgl__context,mcp__tgl__search,mcp__tgl__region,'
         'mcp__tgl__trace',
}
# Claude Code 2.1.283 auto-connects to an IDE when CLAUDE_CODE_SSE_PORT is set, when it detects a
# VS Code or JetBrains terminal (TERM_PROGRAM, FORCE_CODE_TERMINAL), or when autoConnectIde is on;
# CLAUDE_CODE_AUTO_CONNECT_IDE=false switches all of that off (checked first in its code). The IDE
# then adds its own MCP tools, which --strict-mcp-config does not remove.
LAUNCH_SCRIPT = """#!/usr/bin/env bash
# {run_id} (condition {cond}): start Claude Code with pinned options and no IDE connection.
set -eu
echo "=================================================================="
echo "  RUN    {run_id}   (condition {cond}, question {qid})"
echo "  PASTE  {prompt_file}"
echo "=================================================================="
cd {run}
cleared=()
for v in $(compgen -e); do
  case "$v" in
    CLAUDE_CODE_*|CLAUDECODE|CLAUDE_BRIDGE_*|VSCODE_*|TERM_PROGRAM|TERM_PROGRAM_VERSION|FORCE_CODE_TERMINAL|GIT_ASKPASS)
      unset "$v"; cleared+=("$v");;
  esac
done
export CLAUDE_CODE_AUTO_CONNECT_IDE=false
settings=()
for v in {settings}; do
  if [ -n "${{!v+x}}" ]; then settings+=("$v=${{!v}}"); fi
done
echo "cleared: ${{cleared[*]:-none}}; CLAUDE_CODE_AUTO_CONNECT_IDE=false"
echo "model settings in the environment: ${{settings[*]:-none}}"
echo "check in the session: /mcp lists {mcp} and no 'ide'; record /model (model and effort) in meta.json"
exec {launch}
"""
# Non-secret variables that choose the model, reasoning effort or context handling; their values are
# printed so every run records them (Claude Code 2.1.283 reads these names).
SETTINGS_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                 "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                 "CLAUDE_EFFORT", "MAX_THINKING_TOKENS", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
                 "DISABLE_INTERLEAVED_THINKING")


def launch_script(run_id: str, condition: str, run: Path, prompt_file: Path) -> str:
    mcp = "only tgl" if condition in ("T", *ONLY_TOOL) else "no server"
    qid = parse_run_id(run_id)[1]
    return LAUNCH_SCRIPT.format(run_id=run_id, cond=condition, qid=qid, prompt_file=prompt_file,
                                run=shlex.quote(str(run)), mcp=mcp, launch=LAUNCH[condition],
                                settings=" ".join(SETTINGS_VARS))


def write_launch(rows: Sequence[dict[str, str]], dest: Path) -> list[Path]:
    """(Re)write every run's launch script; run directories and materials are left alone."""
    (dest / "launch").mkdir(parents=True, exist_ok=True)
    out = []
    for row in rows:
        path = dest / "launch" / f"{row['run_id']}.sh"
        path.write_text(launch_script(row["run_id"], row["condition"], dest / "runs" / row["run_id"],
                                      dest / "prompts" / f"{row['run_id']}.txt"))
        path.chmod(0o755)
        out.append(path)
    return out
PROMPT = """Answer the question below using only the document named here; do not use outside knowledge.

{material}

Question: {question}

Instructions:
- Answer in English and cover every part of the question.
- After each fact, cite the PDF page it comes from as (p. N), where p. 1 is the first page of the file (not a printed page label). Cite pages only in this form; do not mention tools, file names or block ids in the answer.
- Do not search the web and do not open any file or source other than the document named above.
- If the document does not contain the information asked for, say so explicitly; do not guess or supply a value from elsewhere.
- End with the complete final answer; do not ask follow-up questions.
"""


PROMPT_MULTI = """Answer the question below using only the documents named here; do not use outside knowledge.

{material}

Question: {question}

Instructions:
- Answer in English and cover every part of the question.
- After each fact, cite the document and the PDF page it comes from as (<document>, p. N), with the document named exactly as above and p. 1 the first page of that file (not a printed page label). Cite pages only in this form; do not mention tools or block ids in the answer.
- Do not search the web and do not open any file or source other than the documents named above.
- If the documents do not contain the information asked for, say so explicitly; do not guess or supply a value from elsewhere.
- End with the complete final answer; do not ask follow-up questions.
"""


ENGLISH = "- Answer in English and cover every part of the question.\n"
CHINESE = "- Answer in the language of the question (Chinese) and cover every part of the question.\n"


def chinese(text: str) -> bool:
    """A question written in Chinese: Han characters make up most of its letters."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(has_han(c) for c in letters) / len(letters) > 0.5


def materials_line(condition: str, docs: Sequence[str]) -> str:
    """The material line of PROMPT_MULTI: every document, named the way answers should cite it."""
    def names(ext: str) -> str:
        return ", ".join(f"`{d}{ext}`" for d in docs)
    if condition == "S":
        raise ValueError("condition S takes single-document questions")
    if condition == "T":
        return f"Documents: {names('')}, available through the `tgl` MCP tools."
    if condition == "P":
        return f"Documents: the PDF files {names('.pdf')} in the current directory."
    if condition in ("W", "V"):
        return (f"Documents: the text files {names('.txt')} in the current directory. Each is the full text of one PDF "
                f"in reading order; each page starts with a line `=== PAGE n ===`, where n is the PDF page number.")
    raise ValueError(f"unknown condition {condition!r}")


S_LINE = ("Document: `{doc}`. The folder `doc/` in the current directory holds its full text in reading order, split "
          "into files; start with `doc/INDEX.md`, which lists each section with its PDF pages, size in tokens and the "
          "file:first-last lines to Read. A line `== pN ==` starts PDF page N (a printed page number follows in "
          "parentheses when it differs).")


def material_line(condition: str, doc: str) -> str:
    if condition == "S":
        return S_LINE.format(doc=doc)
    if condition == "T":
        return f"Document: `{doc}`, available through the `tgl` MCP tools."
    if condition == "P":
        return f"Document: the PDF file `{doc}.pdf` in the current directory."
    if condition in ("W", "V"):
        return (f"Document: the text file `{doc}.txt` in the current directory. It is the full text of the PDF in "
                f"reading order; each page starts with a line `=== PAGE n ===`, where n is the PDF page number.")
    raise ValueError(f"unknown condition {condition!r}")


def prompt(condition: str, q: Question) -> str:
    """The run's prompt; a question written in Chinese asks for the answer in Chinese (only that line
    differs, in every condition)."""
    if not q.doc:
        raise ValueError(f"{q.id}: document has no Docling cache")
    if q.other_docs:
        text = PROMPT_MULTI.format(material=materials_line(condition, q.docs), question=q.question)
    else:
        text = PROMPT.format(material=material_line(condition, q.doc), question=q.question)
    return text.replace(ENGLISH, CHINESE, 1) if chinese(q.question) else text


def row_docs(row: dict[str, str]) -> list[str]:
    """Every document of a plan row: its own `doc` first, then `other_docs` (absent in older plans)."""
    return [row["doc"]] + [d for d in (row.get("other_docs") or "").split(";") if d]


def row_materials(row: dict[str, str]) -> list[str]:
    return [m for m in row["material"].split(";") if m]


def run_id(condition: str, qid: str, attempt: int = 1) -> str:
    return f"{condition}-{qid}-{attempt}"


def make_plan(questions: Sequence[Question], conditions: Sequence[str] = DEFAULT_CONDITIONS, stage: str = "pilot",
              seed: int = 0, fulltext_dir: Path | None = None,
              fulltext_v_dir: Path | None = None, export_dir: Path | None = None) -> list[dict[str, str]]:
    rng = random.Random(seed)
    qs = list(questions)
    rng.shuffle(qs)
    rows: list[dict[str, str]] = []
    for q in qs:
        conds = list(conditions)
        rng.shuffle(conds)
        for c in conds:
            rows.append({"order": str(len(rows) + 1), "run_id": run_id(c, q.id), "stage": stage,
                         "condition": c, "question_id": q.id, "split": q.split, "doc": q.doc or "",
                         "material": ";".join(dict.fromkeys(material_path(c, d, fulltext_dir, fulltext_v_dir, export_dir)
                                                            for d in (q.docs or ("",)))),
                         "other_docs": ";".join(q.other_docs)})
    return rows


def material_path(condition: str, doc: str, fulltext_dir: Path | None = None,
                  fulltext_v_dir: Path | None = None, export_dir: Path | None = None) -> str:
    """T: the MCP server; P: the PDF; W, V: the whole-text file; S: the document's folder written by the export
    command (`export <doc> --out <export_dir>/<doc> --as doc` gives <export_dir>/<doc>/doc), copied into the run as doc/."""
    if condition == "T":
        return "mcp:tgl"
    if condition == "S":
        if export_dir is None:
            raise ValueError("condition S needs export_dir")
        return str(export_dir / doc / "doc")
    if condition == "P":
        return str(registry.get(doc).pdf_path)
    if condition == "V":
        return str((fulltext_v_dir or FULLTEXT["V"]) / f"{doc}.txt")
    if condition == "W":
        return str((fulltext_dir or FULLTEXT["W"]) / f"{doc}.txt")
    raise ValueError(f"unknown condition {condition!r}")


def write_plan(rows: Sequence[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, PLAN_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def read_plan(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def mcp_config(snapshot: Path | None, docs: Sequence[str] = (), server_env: dict[str, str] | None = None) -> dict:
    """T's .mcp.json. `server_env` (e.g. the reader's TGLW_* switches) is written in, so every run records
    the settings its server ran with rather than inheriting them unseen."""
    env = {"PYTHONDONTWRITEBYTECODE": "1", **(server_env or {})}
    if snapshot:
        env["PYTHONPATH"] = str(snapshot / "src")
        env["TGL_DATA"] = str(snapshot / "data")  # also pins the data where no research defaults place it there
    if docs:
        env[ALLOWED_DOCS_ENV] = ",".join(docs)
    return {"mcpServers": {"tgl": {"type": "stdio", "command": PYTHON, "args": ["-m", "tglw.mcp_server"],
                                   "env": env}}}


def check_allowlist(mcp_json: Path, docs: Sequence[str]) -> list[str]:
    """Problems with a T run's .mcp.json: its server must be limited to exactly the run's documents, and
    the server code it runs (PYTHONPATH, when set) must know the setting, or the limit silently lapses."""
    try:
        env = json.loads(mcp_json.read_text())["mcpServers"]["tgl"]["env"]
        allowed = env.get(ALLOWED_DOCS_ENV)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [f"{mcp_json}: no environment for the tgl server"]
    problems = []
    if allowed != ",".join(docs):
        problems.append(f"{mcp_json}: {ALLOWED_DOCS_ENV} is {allowed!r}, expected {','.join(docs)!r}")
    if env.get("PYTHONPATH"):
        tools = Path(env["PYTHONPATH"]) / "tglw" / "mcp_server" / "tools.py"
        if not (tools.is_file() and ALLOWED_DOCS_ENV in tools.read_text()):
            problems.append(f"the tgl server in {env['PYTHONPATH']} does not know {ALLOWED_DOCS_ENV}; "
                            "stage with a newer snapshot")
    return problems


def check_materials(rows: Sequence[dict[str, str]]) -> list[str]:
    return [f"{r['run_id']}: missing {m}" for r in rows if r["condition"] != "T"
            for m in row_materials(r) if not (Path(m).is_dir() if r["condition"] == "S" else Path(m).is_file())]


def run_files(row: dict[str, str]) -> list[str]:
    """What a staged run directory holds: .mcp.json (T, R and U), doc/ (S),
    <document>.pdf (P) or <document>.txt (W, V)."""
    cond = row["condition"]
    if cond in ("T", *ONLY_TOOL):
        return [".mcp.json"]
    if cond == "S":
        return ["doc"]
    return [f"{d}{'.pdf' if cond == 'P' else '.txt'}" for d in row_docs(row)]


def stage(rows: Sequence[dict[str, str]], questions: dict[str, Question], dest: Path,
          snapshot: Path | None = None, server_env: dict[str, str] | None = None) -> list[Path]:
    """Create every run directory of a plan; checks all materials first, and on a rerun keeps runs
    that are already staged. Prompts and launch commands go beside the runs, not inside them.
    P and W/V runs get one copy per document of the row, named <document>.pdf or <document>.txt."""
    problems = check_materials(rows)
    if problems:
        raise FileNotFoundError("; ".join(problems))
    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / "plan.csv").exists():
        write_plan(rows, dest / "plan.csv")
    made = []
    for row in rows:
        run = dest / "runs" / row["run_id"]
        cond = row["condition"]
        targets = [run / f for f in run_files(row)]
        if all(t.exists() for t in targets):
            continue
        run.mkdir(parents=True, exist_ok=True)
        if cond == "T":
            targets[0].write_text(json.dumps(mcp_config(snapshot, row_docs(row), server_env), indent=2) + "\n")
        elif cond == "S":
            if len(row_docs(row)) > 1:
                raise ValueError(f"{row['run_id']}: condition S takes single-document questions")
            shutil.copytree(row_materials(row)[0], targets[0])
        else:
            for source, target in zip(row_materials(row), targets, strict=True):
                shutil.copyfile(source, target)
        (dest / "prompts").mkdir(parents=True, exist_ok=True)
        (dest / "prompts" / f"{row['run_id']}.txt").write_text(prompt(cond, questions[row["question_id"]]))
        write_launch([row], dest)
        made.append(run)
    return made


def parse_run_id(rid: str) -> tuple[str, str, int]:
    cond, rest = rid.split("-", 1)
    qid, attempt = rest.rsplit("-", 1)
    if cond not in RUN_CONDITIONS or not attempt.isdigit():
        raise ValueError(f"not a run id: {rid!r}")
    return cond, qid, int(attempt)


def retry(dest: Path, rid: str) -> Path:
    """Stage the next attempt of a run: a new run directory with copies of the first attempt's files
    (material, or .mcp.json with the same snapshot), the same prompt, and a fresh launch script. A
    server setting naming a directory of the run's own (e.g. the rules and trace directories, named
    after the run id) gets one named after the new attempt, created empty where the first one exists."""
    cond, qid, _ = parse_run_id(rid)
    runs = dest / "runs"
    tries = sorted(parse_run_id(p.name)[2] for p in runs.glob(f"{cond}-{qid}-*") if p.is_dir())
    if not tries:
        raise FileNotFoundError(f"no staged attempt of {cond}-{qid} under {runs}")
    first = runs / run_id(cond, qid, tries[0])
    new_id = run_id(cond, qid, tries[-1] + 1)
    new = runs / new_id
    shutil.copytree(first, new)
    cfg = new / ".mcp.json"
    if cfg.is_file():
        d = json.loads(cfg.read_text())
        for server in (d.get("mcpServers") or {}).values():
            env = server.get("env") or {}
            for k, v in env.items():
                if isinstance(v, str) and Path(v).is_absolute() and Path(v).name == first.name:
                    env[k] = str(Path(v).with_name(new_id))
                    if Path(v).is_dir():
                        Path(env[k]).mkdir(mode=0o700)
        cfg.write_text(json.dumps(d, indent=2) + "\n")
    shutil.copyfile(dest / "prompts" / f"{first.name}.txt", dest / "prompts" / f"{new_id}.txt")
    write_launch([{"run_id": new_id, "condition": cond}], dest)
    return new


def records_for(dest: Path) -> Path:
    """Default records directory of a stage: <x>runs/<stage>[/<sub>...] -> <x>records/<stage>[/<sub>...] for
    the last directory above the stage named runs or ending in _runs (a parallel stage's sub-stages and
    lanes sit below <x>runs/<stage>); otherwise <stage>/records."""
    parts = dest.parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "runs" or parts[i].endswith("_runs"):
            return Path(*parts[:i], parts[i][:-4] + "records", *parts[i + 1:])
    return dest / "records"


def lane_records(records: Path) -> list[Path]:
    """The lane directories of a parallel sub-stage's records (lane1, lane2, ...), or [] for a plain stage."""
    if (records / "ledger.csv").exists():
        return []
    lanes = [p for p in records.glob("lane*") if p.name[4:].isdigit() and (p / "ledger.csv").exists()]
    return sorted(lanes, key=lambda p: int(p.name[4:]))


def ledger_rows(records: Path) -> list[dict[str, str]]:
    """A records directory's ledger rows. For a parallel sub-stage (records/lane<i>/ledger.csv and no
    ledger of its own) the lanes' rows, lane by lane, each with "records" naming its lane directory."""
    lanes = lane_records(records)
    if lanes:
        return [r | {"records": str(lane)} for lane in lanes for r in ledger_rows(lane)]
    path = records / "ledger.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def run_records(records: Path, row: dict[str, str]) -> Path:
    """Where a ledger row's run is recorded: its lane directory for a merged parallel ledger."""
    return Path(row.get("records") or records) / row["run_id"]


def final_rows(ledger: Sequence[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Each run's last ledger row, in the order runs first appear ("note" rows are not runs). A
    correction is a later row for the same run, so it overrides the row it corrects, from invalid to
    valid or from valid to invalid."""
    out: dict[str, dict[str, str]] = {}
    for r in ledger:
        if r["status"] != "note":
            out[r["run_id"]] = r
    return out


def next_run(rows: Sequence[dict[str, str]], ledger: Sequence[dict[str, str]]) -> tuple[str, str, int] | None:
    """The run to do next: first, in plan order, any (condition, question) with no ledger entry;
    then the earliest whose latest numbered attempt failed (a retry). Invalid runs are not rerun;
    "note" rows are not runs. Returns (run_id, kind, position in plan) or None when every row is
    settled."""
    state: dict[tuple[str, str], list[dict[str, str]]] = {}
    for r in ledger:
        if r["status"] != "note":
            state.setdefault((r["condition"], r["question_id"]), []).append(r)
    for i, row in enumerate(rows, 1):
        if (row["condition"], row["question_id"]) not in state:
            return row["run_id"], "first attempt", i
    for i, row in enumerate(rows, 1):
        tries = state[(row["condition"], row["question_id"])]
        if any(t["status"] in ("valid", "invalid") for t in tries):
            continue
        numbered = [int(t["attempt"]) for t in tries if t["attempt"].isdigit()]
        cond, qid, _ = parse_run_id(row["run_id"])
        return run_id(cond, qid, max(numbered, default=0) + 1), "retry after failure", i
    return None


def snapshot(dest: Path, src: Path = Path(__file__).resolve().parents[1], data: Path = DATA,
             dirs: Sequence[str] = SNAPSHOT_DATA) -> Path:
    """Copy the code (the installed tglw package) and the data the MCP server reads, so condition T runs
    one fixed version: `<dest>/src/tglw` and `<dest>/data/<task>` for each of `dirs` that exists; T's
    .mcp.json points PYTHONPATH and TGL_DATA there. Writes `<dest>/snapshot.json` with every file's
    sha256 and each task's MANIFEST."""
    if dest.exists():
        raise FileExistsError(dest)
    shutil.copytree(src, dest / "src" / "tglw", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    dirs = [d for d in dirs if (data / d).is_dir()]
    for d in dirs:
        shutil.copytree(data / d, dest / "data" / d, ignore=shutil.ignore_patterns("wip", "__pycache__"))
    files = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(dest.rglob("*")) if p.is_file()}
    manifests = {d: json.loads((data / d / "MANIFEST.json").read_text()).get("version")
                 for d in dirs if (data / d / "MANIFEST.json").exists()}
    (dest / "snapshot.json").write_text(json.dumps({"manifests": manifests, "files": files}, indent=1) + "\n")
    return dest
