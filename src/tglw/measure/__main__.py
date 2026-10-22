"""Command line: count turns, tokens, tool calls, and images in Claude Code session logs.

    python -m tglw.measure ~/.claude/projects/<project> --out /tmp/measure
    python -m tglw.measure path/to/session.jsonl --out /tmp/measure

--out is required, so a run never writes into a delivered data/ directory by accident.

Only counts and sizes are written; no message text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .calib import calibrate
from .log import discover, mark_copies, parse, subagent_logs
from .report import report_markdown
from .summary import call_rows, summarize


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tglw.measure")
    parser.add_argument("targets", nargs="+", type=Path, help="session .jsonl files or project directories")
    parser.add_argument("--out", type=Path, required=True, help="output directory (required)")
    parser.add_argument("--no-subagents", action="store_true")
    args = parser.parse_args(argv)
    sessions = []
    for target in args.targets:
        for main_log in discover(target.expanduser()):
            sessions.append(parse(main_log))
            if not args.no_subagents:
                sessions += [parse(p, kind) for p, kind in subagent_logs(main_log)]
    mark_copies(sessions)
    rows = [summarize(s) for s in sessions]
    calib = calibrate(sessions)
    write(args.out / "sessions.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    write(args.out / "calls.jsonl", "".join(json.dumps(c) + "\n" for s in sessions for c in call_rows(s)))
    write(args.out / "calibration.json", json.dumps(calib, ensure_ascii=False, indent=1) + "\n")
    write(args.out / "summary.md", report_markdown(rows, calib))
    print(f"{len(rows)} sessions -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
