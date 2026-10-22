"""Library index commands.

    python -m tglw.corpus build [--stale] [NAME ...]   # (re)index documents; default every registered one
    python -m tglw.corpus update NAME                  # one document (what ingestion calls)
    python -m tglw.corpus drop NAME
    python -m tglw.corpus stale                        # documents whose index rows are out of date
    python -m tglw.corpus search "query" [-k 5] [--unit block|page]
    python -m tglw.corpus list DOC "exact text" [--offset N]   # every literal match, addresses only
    python -m tglw.corpus info

The index is TGLW_CORPUS_DB, default <data>/corpus/corpus.sqlite.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import rank, store, view


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tglw.corpus", description="library index over every document")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("names", nargs="*")
    b.add_argument("--stale", action="store_true", help="only documents not indexed or changed since")
    sub.add_parser("update").add_argument("name")
    sub.add_parser("drop").add_argument("name")
    sub.add_parser("stale")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--unit", choices=("block", "page"), default="block")
    li = sub.add_parser("list")
    li.add_argument("doc")
    li.add_argument("text")
    li.add_argument("--offset", type=int, default=0)
    sub.add_parser("info")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        out = store.build(args.names or None, only_stale=args.stale)
        print(f"indexed {len(out)} documents, {sum(out.values())} units -> {store.db_path()}")
    elif args.cmd == "update":
        print(f"{args.name}: {store.update(args.name)} units -> {store.db_path()}")
    elif args.cmd == "drop":
        print(f"{args.name}: {store.drop(args.name)} units removed")
    elif args.cmd == "stale":
        rows = store.stale()
        print("\n".join(f"{n}: {why}" for n, why in rows.items()) or "index up to date")
        return 1 if rows else 0
    elif args.cmd == "search":
        print(view.search_text(args.query, k=args.k, unit=args.unit))
    elif args.cmd == "list":
        from .listing import list_literal
        print(list_literal(args.doc, args.text, offset=args.offset))
    elif args.cmd == "info":
        print(json.dumps(rank.index_info(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
