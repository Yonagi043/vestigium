"""The ingest command: add PDF... | add --sample | rebuild NAME...|--all | list | remove NAME | qc NAME
(a bare `PDF...` or `--sample` means add). Run as `<COMMAND> ingest ...` or `python -m tglw.ingest ...`.

--sample adds the bundled sample document(s) from tglw.samples; their Docling bundles ship with the
package, so this works without Docling.

Exit status: 0 all done, 1 some document failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tglw import COMMAND, HOME_ENV
from tglw.common import paths, registry

from . import convert, library, qc, run

COMMANDS = ("add", "rebuild", "list", "remove", "qc")
PROG = f"{COMMAND} ingest"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description=f"library: {paths.HOME} (set {HOME_ENV} to use another)"
                                 if not paths.RESEARCH else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help=f"convert PDFs with Docling and make them readable through `{COMMAND} mcp`")
    a.add_argument("pdfs", nargs="*", type=Path)
    a.add_argument("--sample", action="store_true", help="add the bundled sample document (no Docling needed)")
    a.add_argument("--name", help="registered name (one PDF only; default: from the file name)")
    a.add_argument("--cls", default=library.DEFAULT_CLS, help="document class; 'nist' resolves NIST SP 800-53 controls")
    a.add_argument("--ocr", choices=convert.ENGINES, default="rapidocr")
    a.add_argument("--page-images", action="store_true", help="keep page images in the Docling cache (much larger)")
    a.add_argument("--device", default="auto", help="Docling device: auto, cpu, cuda, cuda:N")
    a.add_argument("--force", action="store_true", help="convert again and rebuild even if already in the library")
    a.add_argument("--scan-text-ocr", choices=("fill", "off"), default="fill",
                   help="scanned pages that carry their own text layer: OCR fills its gaps (fill) or is skipped (off)")
    b = sub.add_parser("rebuild", help="rebuild sidecars, index and report from the cached Docling bundles")
    b.add_argument("names", nargs="*")
    b.add_argument("--all", action="store_true")
    sub.add_parser("list", help="documents in the library")
    r = sub.add_parser("remove", help="remove a document and its files")
    r.add_argument("name")
    r.add_argument("--keep-cache", action="store_true", help="keep the Docling bundle")
    q = sub.add_parser("qc", help="show a document's quality report")
    q.add_argument("name")
    q.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in (*COMMANDS, "-h", "--help"):
        argv.insert(0, "add")
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "add":
            if not args.pdfs and not args.sample:
                print(f"{PROG} add: give PDF files or --sample", file=sys.stderr)
                return 2
            outcomes = run.add_samples() if args.sample else []
            outcomes += run.add(args.pdfs, args.name, args.cls, args.ocr, args.page_images, args.device, args.force,
                                args.scan_text_ocr)
            return 1 if any(o.status == "failed" for o in outcomes) else 0
        if args.cmd == "rebuild":
            names = [d.name for d in registry.docs()] if args.all else args.names
            if not names:
                print(f"{PROG} rebuild: give document names or --all", file=sys.stderr)
                return 2
            return 1 if any(o.status == "failed" for o in run.rebuild(names)) else 0
        if args.cmd == "list":
            for d in registry.docs(cached_only=False):
                report = qc.load(d.name)
                status = report["verdict"]["status"] if report else "-"
                print(f"{d.name} · {d.cls} · {d.pages} p · quality {status}" + (f" · from {d.source}" if d.source else ""))
            return 0
        if args.cmd == "remove":
            for line in run.remove(args.name, args.keep_cache):
                print(line)
            return 0
        report = qc.load(args.name)
        if report is None:
            print(f"no quality report for {args.name!r}", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else qc.render(report))
        return 0
    except (library.LibraryError, KeyError) as err:
        print(f"{PROG}: {err}", file=sys.stderr)
        return 2 if isinstance(err, library.LibraryError) else 1


if __name__ == "__main__":
    sys.exit(main())
