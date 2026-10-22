"""python -m tglw.score <command>, the same as the installed command's `eval`; see `--help` of each command.

Questions are the development files (plus the 6x10 set in the research checkout) unless --h-path is
given; a question file inside a held-out directory (see tglw.score.settings) is refused unless
--allow-heldout is passed (final acceptance only).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import sys
import time
from pathlib import Path

from tglw import COMMAND

from . import (answer, audit, compare, costs, evidence, fulltext, grading, headless, parallel, plan, settings, sheet,
               sources, summary)
from .questions import Question, h_dev_files, load_all, load_h

try:  # research material (task E's cost model), not released
    from . import estimate
except ImportError:
    estimate = None


def _questions(args: argparse.Namespace) -> dict[str, Question]:
    return load_all(args.h_path or None, args.allow_frozen, not args.no_6x10)


def _qargs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--h-path", type=Path, action="append", help="question file, JSONL (repeatable)")
    p.add_argument("--allow-heldout", "--allow-frozen", dest="allow_frozen", action="store_true",
                   help="permit question files in held-out directories (final acceptance)")
    p.add_argument("--no-6x10", action="store_true", help="leave the 6x10 set out (research checkout)")


def cmd_list(args: argparse.Namespace) -> int:
    for q in _questions(args).values():
        kind = "unanswerable" if q.unanswerable else f"{len(q.required)} required"
        print(f"{q.id}\t{q.split}\t{'+'.join(q.docs) or '-'}\t{kind}")
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    q = _questions(args)[args.question]
    md, cv = sheet.write(sheet.build(args.run, args.condition, q, args.answer.read_text()), args.out)
    print(md, cv, sep="\n")
    return 0


RECORDS_HELP = "records directory (default: <x>records/<stage> beside <x>runs/<stage>, else <stage>/records)"
LEDGER_COLUMNS = ("run_id", "condition", "question_id", "attempt", "status", "reason")


def _attempts(records: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Ledger rows by (condition, question), merged over the lanes of a parallel sub-stage; status is
    valid, failed or invalid ("note" rows, such as the start of headless mode, are skipped)."""
    out: dict[tuple[str, str], list[dict[str, str]]] = {}
    for r in plan.ledger_rows(records):
        if r["status"] == "note":
            continue
        if r["status"] not in ("valid", "failed", "invalid"):
            raise ValueError(f"{plan.run_records(records, r).parent / 'ledger.csv'}: {r['run_id']} has status "
                             f"{r['status']!r}")
        out.setdefault((r["condition"], r["question_id"]), []).append(r)
    return out


def cmd_sheets(args: argparse.Namespace) -> int:
    """One sheet per (condition, question) of the plan: the last valid attempt in records/ledger.csv,
    judged by each run's last ledger row so that a correction counts (without a ledger, attempt 1).
    Existing sheets are kept. With --blind, sheets get random codes, are written in random order with
    one timestamp, and the key is written last; an existing key is never overwritten."""
    qs = _questions(args)
    ledger = _attempts(args.runs)
    if not ledger:
        print("warning: no ledger.csv; using attempt 1 of every run", file=sys.stderr)
    if args.blind and args.blind.exists():
        print(f"{args.blind} exists; refusing to overwrite a blinding key", file=sys.stderr)
        return 1
    if args.blind:
        args.blind.parent.mkdir(parents=True, exist_ok=True)  # before any sheet is written
    rows = plan.read_plan(args.plan)
    if args.blind:
        secrets.SystemRandom().shuffle(rows)
    key: list[tuple[str, str, str]] = []
    made: list[Path] = []
    skipped: list[str] = []
    for row in rows:
        cond, qid = row["condition"], row["question_id"]
        tries = list(plan.final_rows(ledger.get((cond, qid), [])).values())
        valid = [t for t in tries if t["status"] == "valid"]
        skipped += [f"{t['run_id']} ({t['status']}: {t['reason']})" for t in tries if t["status"] != "valid"]
        rid = valid[-1]["run_id"] if valid else (row["run_id"] if not ledger else "")
        rec = (plan.run_records(args.runs, valid[-1]) if valid else args.runs / rid) if rid else None
        if rec is None:
            skipped.append(f"{cond}-{qid}: no valid attempt")
            continue
        ans = rec / "answer.md"
        if not ans.exists() and (rec / "session.jsonl").exists():
            try:
                got = answer.final_answer(rec / "session.jsonl")
            except ValueError as e:
                skipped.append(f"{rid}: {e}")
                continue
            ans.write_text(got.text)
            if got.warning:
                print(f"warning: {rid}: {got.warning}", file=sys.stderr)
        if not ans.exists():
            skipped.append(f"{rid}: no answer")
            continue
        name, shown = rid, cond
        if args.blind:
            name = "x" + secrets.token_hex(5)
            key.append((name, rid, cond))
            shown = "?"
        if not args.blind and (args.out / f"{name}.csv").exists():
            continue
        made += sheet.write(sheet.build(name, shown, qs[qid], ans.read_text()), args.out)
    if args.blind:
        stamp = time.time()
        for path in made:
            os.utime(path, (stamp, stamp))
        tmp = args.blind.with_suffix(".tmp")
        with open(tmp, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(("code", "run_id", "condition"))
            w.writerows(sorted(key))
        os.replace(tmp, args.blind)
    for s_ in skipped:
        print(f"skipped\t{s_}")
    print(f"{len(made) // 2} sheets written, {len(skipped)} skipped")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    docs = [d for d in (args.docs or "").split(";") if d]
    lines, ran = audit.report(args.session, args.condition, args.run_dir, docs)
    print("\n".join(lines))
    return 1 if ran else 0


def cmd_summarize(args: argparse.Namespace) -> int:
    rows = summary.read_sheets(args.files)
    if args.key:
        rows = summary.unblind(rows, args.key)
    text = summary.report(rows)
    unfilled = sum(len(t.unfilled) for t in summary.tally(rows).values())
    print(text)
    if unfilled and not args.allow_unfilled:
        print(f"{unfilled} rows have no verdict; nothing written (use --allow-unfilled to write anyway)",
              file=sys.stderr)
        return 1
    if args.out:
        args.out.write_text(text)
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    """One sheet: {"verdicts": [...]}. Several (args.sheet a directory): {"sheets": [{"code", "verdicts"}]};
    with --expect the codes must be exactly those, and nothing is written otherwise."""
    text = args.verdicts.read_text().strip()
    data = json.loads(text.removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    if isinstance(data, dict) and "sheets" in data:
        got = {s["code"]: s["verdicts"] for s in data["sheets"]}
        if len(got) != len(data["sheets"]) or (args.expect and sorted(got) != sorted(args.expect.split(","))):
            raise ValueError(f"codes {sorted(got)} differ from the expected {args.expect}")
        missing = [c for c in got if not (args.sheet / f"{c}.csv").exists()]
        if missing:
            raise ValueError(f"no sheet for {missing} in {args.sheet}")
        for code, verdicts in got.items():
            sheet.fill(args.sheet / f"{code}.csv", verdicts)
            print(args.sheet / f"{code}.csv")
        return 0
    sheet.fill(args.sheet, data["verdicts"] if isinstance(data, dict) else data)
    print(args.sheet)
    return 0


def cmd_costs(args: argparse.Namespace) -> int:
    text = costs.table(costs.valid_runs(plan.read_plan(args.plan), args.records))
    print(text, end="")
    if args.out:
        args.out.write_text(text)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    _questions(args)
    drop = set(args.exclude or ())
    rows = [r for r in summary.unblind(summary.read_sheets(args.files), args.key) if r["question_id"] not in drop]
    regrade = summary.unblind(summary.read_sheets(args.regrade), args.key) if args.regrade else []
    regrade = [r for r in regrade if r["question_id"] not in drop]
    plan_rows = [r for r in plan.read_plan(args.plan) if r["question_id"] not in drop]
    types = compare.load_types(args.h_path or h_dev_files())
    text = compare.report(rows, plan_rows, args.records, types, regrade, not args.no_per_question,
                          args.fulltext_index or compare.FULLTEXT_INDEX)
    if drop:
        text = f"> 排除的题（三种条件一起排除）：{len(drop)} 道\n\n" + text
    print(text, end="")
    if args.out:
        args.out.write_text(text)
    return 0


def cmd_fulltext(args: argparse.Namespace) -> int:
    print(fulltext.write_all(args.out, args.doc or None, args.width, args.zh_clean))
    return 0


def cmd_fold(args: argparse.Namespace) -> int:
    index = fulltext.fold_dir(args.src, args.out, args.width)
    print(f"folded texts -> {args.out}; index {index}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    qs = _questions(args)
    picked = list(qs.values()) if args.questions == ["all"] else [qs[i] for i in args.questions]
    rows = plan.make_plan(picked, args.conditions.split(","), args.stage, args.seed,
                          args.fulltext, args.fulltext_v, args.export)
    plan.write_plan(rows, args.out)
    print(f"{len(rows)} runs -> {args.out}")
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    if not args.snapshot:
        print("warning: no --snapshot; condition T will run the live code of this checkout", file=sys.stderr)
    made = plan.stage(plan.read_plan(args.plan), _questions(args), args.dest, args.snapshot,
                      dict(x.split("=", 1) for x in args.server_env or ()))
    print(f"{len(made)} run directories created under {args.dest / 'runs'}")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    for path in plan.write_launch(plan.read_plan(args.plan), args.dest):
        print(path)
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    plan_file = args.plan or args.dest / "plan.csv"
    records = args.records or plan.records_for(args.dest)
    rows = plan.read_plan(plan_file)
    nxt = plan.next_run(rows, plan.ledger_rows(records))
    if nxt is None:
        print(f"all {len(rows)} runs of {plan_file} are settled in {records / 'ledger.csv'}")
        return 0
    rid, kind, pos = nxt
    cond, qid, _ = plan.parse_run_id(rid)
    print(f"next: {rid} ({kind}; plan row {pos} of {len(rows)}; condition {cond}; question {qid})")
    if not (args.dest / "runs" / rid).exists():
        print(f"stage it first:\n  {COMMAND} eval retry {plan.run_id(cond, qid, 1)} --dest {args.dest}")
    print("copy these two commands:")
    print(f"  bash {args.dest / 'launch' / f'{rid}.sh'}")
    print(f"  cat {args.dest / 'prompts' / f'{rid}.txt'}    # paste its whole text into the session")
    print(f"afterwards record the run under {records / rid}/ and add a row to {records / 'ledger.csv'}")
    return 0


def cmd_headless(args: argparse.Namespace) -> int:
    return headless.run_batch(args.dest, args.plan or args.dest / "plan.csv", args.records or plan.records_for(args.dest),
                              only=args.only, pause=args.pause, timeout=args.timeout, max_turns=args.max_turns,
                              dry_run=args.dry_run, claude=args.claude, allow_frozen=args.allow_frozen,
                              config_dir=args.config_dir, model=args.model, stop_on_denials=not args.no_stop_on_denials)


def cmd_parallel(args: argparse.Namespace) -> int:
    return parallel.run_parallel(args.dest, args.records or plan.records_for(args.dest), args.config_dir,
                                 pause=args.pause, timeout=args.timeout, max_turns=args.max_turns, claude=args.claude,
                                 allow_frozen=args.allow_frozen, dry_run=args.dry_run, retry_infra=args.retry_infra)


def cmd_retry(args: argparse.Namespace) -> int:
    print(plan.retry(args.dest, args.run_id))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    print(plan.snapshot(args.dest, dirs=args.data or plan.SNAPSHOT_DATA))
    return 0


def cmd_stage_parallel(args: argparse.Namespace) -> int:
    spec = json.loads(args.spec.read_text())
    subs = parallel.substages_from_spec(spec, load_h)
    made = parallel.stage_parallel(Path(spec["dest"]), subs, int(spec.get("lanes", 3)), int(spec.get("seed", 0)))
    print(f"{len(made)} run directories in {len(subs)} sub-stages under {spec['dest']}; schedule {Path(spec['dest']) / 'schedule.csv'}")
    return 0


def cmd_evidence_images(args: argparse.Namespace) -> int:
    from tglw.common import registry

    made = evidence.write_crops(evidence.read_rows(args.h_path), args.out, lambda d: registry.get(d).pdf_path, args.dpi)
    print(f"{len(made)} screenshots -> {args.out}")
    return 0


def cmd_grading_queue(args: argparse.Namespace) -> int:
    entries = grading.make_queue(args.sheets, args.fulltext, args.images, args.seed)
    grading.write_queue(entries, args.out)
    print(f"{len(entries)} graders, {sum(len(e['codes']) for e in entries)} sheets -> {args.out}")
    return 0


def cmd_grader_prompt(args: argparse.Namespace) -> int:
    entries = json.loads(args.queue.read_text())
    entry = next((e for e in entries if e["n"] == args.n), None)
    if entry is None:
        raise SystemExit(f"no entry {args.n} in {args.queue}")
    print(grading.prompt(entry, args.sheets or args.queue.parent / "sheets"))
    return 0


def cmd_fetch_docs(args: argparse.Namespace) -> int:
    rows = sources.load(args.sources)
    got = sources.fetch(rows, args.out, args.doc or (), pause=args.pause)
    bad = [c for c in got if c.status not in sources.VERIFIED]
    if args.ingest_script:
        picked = [r for r in rows if not args.doc or r["name"] in args.doc]
        args.ingest_script.write_text(sources.ingest_script(picked, args.out.resolve()))
        args.ingest_script.chmod(0o755)
        print(f"ingest commands -> {args.ingest_script}")
    print(f"{len(got) - len(bad)} of {len(got)} documents verified in {args.out}")
    return 1 if bad else 0


def cmd_prompts(args: argparse.Namespace) -> int:
    qs = _questions(args)
    args.out.mkdir(parents=True, exist_ok=True)
    rows = plan.read_plan(args.plan)
    for row in rows:
        (args.out / f"{row['run_id']}.txt").write_text(plan.prompt(row["condition"], qs[row["question_id"]]))
    print(f"{len(rows)} prompts -> {args.out}")
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    print(estimate.table(args.cases, args.overhead or [estimate.J_F], args.scale_to), end="")
    print(f"\nJ: F = {estimate.J_F_CLAUDE:,} Claude tokens = {estimate.J_F:,} o200k at {estimate.J_RATIO}; "
          f"a 200k-token context holds documents up to {estimate.window_o200k():,} o200k tokens")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    got = answer.final_answer(args.session)
    if got.warning:
        print(f"warning: {got.warning}", file=sys.stderr)
    if args.out:
        args.out.write_text(got.text)
    else:
        print(got.text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=f"{COMMAND} eval")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list scorable questions")
    _qargs(p)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("sheet", help="grading sheet for one answer")
    p.add_argument("run")
    p.add_argument("condition")
    p.add_argument("question")
    p.add_argument("answer", type=Path)
    p.add_argument("--out", type=Path, required=True)
    _qargs(p)
    p.set_defaults(fn=cmd_sheet)

    p = sub.add_parser("sheets", help="grading sheets for every answered run of a plan")
    p.add_argument("plan", type=Path)
    p.add_argument("runs", type=Path, help="records directory: ledger.csv and <run_id>/answer.md or session.jsonl")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--blind", type=Path, help="write sheets under random codes; key file goes here (keep it away "
                   "from graders)")
    _qargs(p)
    p.set_defaults(fn=cmd_sheets)

    p = sub.add_parser("summarize", help="accuracy from filled sheets (.csv or .md)")
    p.add_argument("files", type=Path, nargs="+")
    p.add_argument("--out", type=Path)
    p.add_argument("--allow-unfilled", action="store_true")
    p.add_argument("--key", type=Path, help="key file from `sheets --blind`")
    p.set_defaults(fn=cmd_summarize)

    p = sub.add_parser("fill", help="write a grader's verdicts (JSON) into a sheet's CSV")
    p.add_argument("sheet", type=Path)
    p.add_argument("verdicts", type=Path, help='{"verdicts": [{"facet_id", "verdict", "note"}, ...]}, or '
                   '{"sheets": [{"code", "verdicts"}, ...]} with a directory of sheets')
    p.add_argument("--expect", help="comma-separated codes the grader was given (several sheets)")
    p.set_defaults(fn=cmd_fill)

    p = sub.add_parser("costs", help="tokens, time and tool calls per condition from the run records")
    p.add_argument("plan", type=Path)
    p.add_argument("records", type=Path)
    p.add_argument("--out", type=Path)
    p.set_defaults(fn=cmd_costs)

    p = sub.add_parser("compare", help="paired comparison of conditions: accuracy, costs, breakdowns (PROTOCOL 7)")
    p.add_argument("files", type=Path, nargs="+", help="graded sheets (.csv)")
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--records", type=Path, required=True)
    p.add_argument("--regrade", type=Path, nargs="*", help="second grading of some sheets (same codes)")
    p.add_argument("--exclude", action="append", help="question id to leave out in every condition (repeatable)")
    p.add_argument("--no-per-question", action="store_true", help="leave out the table listing each question")
    p.add_argument("--fulltext-index", type=Path, help="index.csv of `fulltext`, for the 200k-window breakdown")
    p.add_argument("--out", type=Path)
    _qargs(p)
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("audit", help="flag tool calls outside a run's condition (names and paths only)")
    p.add_argument("session", type=Path)
    p.add_argument("--condition", required=True, choices=plan.CONDITIONS)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--docs", help="documents the prompt names, separated by ';' (a plan row's doc and other_docs); "
                                  "a T call naming any other document is flagged")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("fulltext", help="condition W: whole-document text files")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--doc", action="append", help="registry name (repeatable); default all cached docs")
    p.add_argument("--width", type=int, default=fulltext.WIDTH)
    p.add_argument("--zh-clean", action="store_true", help="V′-zh: the Chinese display clean-up, breaks inside "
                   "Chinese sentences joined (only whitespace changes; checked)")
    p.set_defaults(fn=cmd_fulltext)

    p = sub.add_parser("fold", help="fold whole-document texts so no line exceeds --width (condition W′)")
    p.add_argument("--src", type=Path, required=True, help="directory of texts from `fulltext`")
    p.add_argument("--out", type=Path, required=True, help="new directory (refused if it exists)")
    p.add_argument("--width", type=int, default=fulltext.FOLD_WIDTH)
    p.set_defaults(fn=cmd_fold)

    p = sub.add_parser("plan", help="seeded run order for questions x conditions")
    p.add_argument("questions", nargs="+", help="question ids, or `all` for every question loaded")
    p.add_argument("--conditions", default=",".join(plan.DEFAULT_CONDITIONS),
                   help=f"comma-separated, from {','.join(plan.CONDITIONS)} (default T,P,W)")
    p.add_argument("--stage", default="pilot")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--fulltext", type=Path, help="directory of W texts (default data/K/fulltext)")
    p.add_argument("--fulltext-v", type=Path, help="directory of V texts (default data/W2/fulltext)")
    p.add_argument("--export", type=Path, help=f"condition S: directory with <document>/doc/ from {COMMAND} export --as doc")
    p.add_argument("--out", type=Path, required=True)
    _qargs(p)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("stage", help="create run directories for a plan (operator, when approved)")
    p.add_argument("plan", type=Path)
    p.add_argument("--dest", type=Path, required=True)
    p.add_argument("--snapshot", type=Path, help="server snapshot from `snapshot` for condition T")
    p.add_argument("--server-env", action="append", metavar="NAME=VALUE",
                   help="setting for T's server, written into .mcp.json (repeatable), e.g. TGLW_CJK=1")
    _qargs(p)
    p.set_defaults(fn=cmd_stage)

    p = sub.add_parser("launch", help="rewrite the launch scripts of a staged plan (runs untouched)")
    p.add_argument("plan", type=Path)
    p.add_argument("--dest", type=Path, required=True)
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("next", help="which run to do next, from the plan order and the ledger")
    p.add_argument("--dest", type=Path, required=True, help="stage directory (<runs>/<stage>)")
    p.add_argument("--plan", type=Path, help="plan file (default <dest>/plan.csv)")
    p.add_argument("--records", type=Path, help=RECORDS_HELP)
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("headless", help="run a staged plan with `claude -p`, one run at a time (calls the model)")
    p.add_argument("--dest", type=Path, required=True, help="stage directory (<runs>/<stage>)")
    p.add_argument("--plan", type=Path, help="plan file (default <dest>/plan.csv)")
    p.add_argument("--records", type=Path, help=RECORDS_HELP)
    p.add_argument("--only", help="run just this run id")
    p.add_argument("--pause", type=float, default=60, help="seconds between runs (default 60)")
    p.add_argument("--timeout", type=float, default=900, help="seconds before a run is killed (default 900)")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")
    p.add_argument("--allow-heldout", "--allow-frozen", dest="allow_frozen", action="store_true",
                   help="permit a plan with held-out questions, or a held-out stage")
    p.add_argument("--config-dir", type=Path,
                   help="CLAUDE_CONFIG_DIR for the sessions (required for held-out plans; .claude-<prefix>* for a "
                        "held-out stage)")
    p.add_argument("--model", help="full model id passed as --model (default: the user's settings)")
    p.add_argument("--claude", default=settings.CLAUDE, help="Claude Code command (default: TGLW_EVAL_CLAUDE or claude)")
    p.add_argument("--no-stop-on-denials", action="store_true",
                   help="refused permission requests do not stop the batch (they are still recorded and audited)")
    p.set_defaults(fn=cmd_headless)

    p = sub.add_parser("parallel", help="run a parallel stage: its lanes at once, each one run at a time, one shared "
                       "stop file (calls the model)")
    p.add_argument("--dest", type=Path, required=True, help="parallel stage directory (<runs>/<stage>, with schedule.csv)")
    p.add_argument("--records", type=Path, help=RECORDS_HELP)
    p.add_argument("--config-dir", type=Path, required=True,
                   help="lane i runs with CLAUDE_CONFIG_DIR=<config-dir>-<i>, e.g. ~/.claude-eval -> ~/.claude-eval-1")
    p.add_argument("--claude", default=settings.CLAUDE, help="Claude Code command (default: TGLW_EVAL_CLAUDE or claude)")
    p.add_argument("--pause", type=float, default=60, help="seconds between runs within a lane (default 60)")
    p.add_argument("--timeout", type=float, default=900, help="seconds before a run is killed (default 900)")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--allow-heldout", "--allow-frozen", dest="allow_frozen", action="store_true",
                   help="permit held-out questions and held-out stages")
    p.add_argument("--dry-run", action="store_true", help="print each lane's commands; run nothing")
    p.add_argument("--retry-infra", action="store_true",
                   help="give an infrastructure failure (API error, crash, error result, no session log) one more "
                        "attempt with the same material; timeouts, turn limits and missing answers are never rerun")
    p.set_defaults(fn=cmd_parallel)

    p = sub.add_parser("retry", help="stage the next attempt of a run (same material, prompt, snapshot)")
    p.add_argument("run_id", help="any attempt of the run, e.g. P-<question>-1")
    p.add_argument("--dest", type=Path, required=True)
    p.set_defaults(fn=cmd_retry)

    p = sub.add_parser("snapshot", help="freeze code and delivered data for condition T (operator)")
    p.add_argument("--dest", type=Path, required=True)
    p.add_argument("--data", action="append",
                   help=f"data directory to copy (repeatable; default {', '.join(plan.SNAPSHOT_DATA)})")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("stage-parallel", help="stage a parallel stage from a JSON spec (sub-stages, lanes, seeds)")
    p.add_argument("spec", type=Path, help='{"dest", "lanes", "seed", "allow_heldout"?, "substages": [{"name", '
                   '"questions", "only"?, "conditions", "model", "seed", "snapshot"?, "fulltext"?, "fulltext_v"?, "export"?, "server_env"?}]}')
    p.set_defaults(fn=cmd_stage_parallel)

    p = sub.add_parser("evidence-images", help="screenshots of the image evidence of visual questions, for graders")
    p.add_argument("--h-path", type=Path, action="append", required=True, help="question file, JSONL (repeatable)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--dpi", type=int, default=evidence.DPI)
    p.set_defaults(fn=cmd_evidence_images)

    p = sub.add_parser("grading-queue", help="one grader per question: its blind sheets, document texts, screenshots")
    p.add_argument("sheets", type=Path, help="directory of blind sheets from `sheets --blind`")
    p.add_argument("--fulltext", type=Path, required=True, help="directory of whole-document texts the graders search")
    p.add_argument("--images", type=Path, help="directory of evidence screenshots from `evidence-images`")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path, required=True, help="queue file (JSON)")
    p.set_defaults(fn=cmd_grading_queue)

    p = sub.add_parser("grader-prompt", help="print the prompt for one grader of a queue")
    p.add_argument("queue", type=Path)
    p.add_argument("n", type=int, help="entry number")
    p.add_argument("--sheets", type=Path, help="sheet directory (default: sheets/ beside the queue)")
    p.set_defaults(fn=cmd_grader_prompt)

    p = sub.add_parser("fetch-docs", help="download the public PDFs of a source list and check their sha256")
    p.add_argument("sources", type=Path, help="source list (JSON), e.g. documents.json of the public questions")
    p.add_argument("--out", type=Path, required=True, help="directory for the PDFs")
    p.add_argument("--doc", action="append", help="registry name (repeatable; default every document)")
    p.add_argument("--pause", type=float, default=2.0, help="seconds between downloads (default 2)")
    p.add_argument("--ingest-script", type=Path, help=f"also write a shell script that runs {COMMAND} ingest on each PDF "
                   "under the name the questions use")
    p.set_defaults(fn=cmd_fetch_docs)

    p = sub.add_parser("prompts", help="write the prompt text of every plan row (no run directories)")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    _qargs(p)
    p.set_defaults(fn=cmd_prompts)

    if estimate:
        p = sub.add_parser("estimate", help="cost estimate from task E's round-trip model (research checkout)")
        p.add_argument("cases", nargs="*", help="h-dev case ids (answerable)")
        p.add_argument("--overhead", type=int, action="append",
                       help="fixed o200k tokens per turn F (repeatable; default J's 19,338)")
        p.add_argument("--scale-to", type=int, help="mean over all h-dev cases times this many questions")
        p.set_defaults(fn=cmd_estimate)

    p = sub.add_parser("answer", help="final answer text from a Claude Code session log")
    p.add_argument("session", type=Path)
    p.add_argument("--out", type=Path)
    p.set_defaults(fn=cmd_answer)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
