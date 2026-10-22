"""pipeline {build,check,compare} ...  (see tglw.pipeline); main(argv) returns the exit status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tglw import COMMAND
from tglw.common import registry

from . import run


def _names(args: argparse.Namespace) -> list[str]:
    return [] if args.all else args.docs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=f"{COMMAND} pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild sidecars B -> A -> C1 -> C2 -> D1 -> D2 -> M into --out")
    b.add_argument("docs", nargs="*")
    b.add_argument("--all", action="store_true")
    b.add_argument("--out", type=Path, default=run.DEFAULT_OUT)
    c = sub.add_parser("check", help="completeness, version consistency and MCP readability")
    c.add_argument("docs", nargs="*")
    c.add_argument("--all", action="store_true")
    c.add_argument("--data", type=Path, default=None, help="data root to check (default: the delivered data/)")
    c.add_argument("--no-mcp", action="store_true")
    c.add_argument("--json", type=Path, default=None)
    k = sub.add_parser("compare", help="compare a build with the delivered sidecars")
    k.add_argument("--run", type=Path, default=run.DEFAULT_OUT)
    k.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        if not args.all and not args.docs:
            ap.error("give document names or --all")
        unknown = [n for n in args.docs if n not in {d.name for d in registry.docs()}]
        if unknown:
            ap.error(f"not registered (data/common/docs.json): {unknown}")
        man = run.build(_names(args), args.out)
        print(json.dumps({k: man[k] for k in ("mode", "steps", "sidecars")}, ensure_ascii=False, indent=1))
    elif args.cmd == "check":
        from . import check

        if not args.all and not args.docs:
            ap.error("give document names or --all")
        check.main(_names(args), args.data, mcp=not args.no_mcp, json_out=args.json)
    else:
        from . import compare

        compare.main(args.run, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
