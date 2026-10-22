"""The export subcommand (tglw.COMMAND export): write documents as plain files for shells without MCP.

    <command> export DOC [DOC ...] --out DIR
    <command> export DOC --out DIR --as SHORT
    <command> export --all --out DIR

Each document goes to DIR/<name>/ (or DIR/SHORT/; an older export there is replaced once the new one
is complete); DIR/README.md lists the exported documents and how to read them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tglw import COMMAND
from tglw.common import registry

from . import build, fold, write


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=f"{COMMAND} export",
                                 description=f"{COMMAND} export: write documents as plain files for shells without MCP.")
    ap.add_argument("docs", nargs="*", help="registered document names")
    ap.add_argument("--all", action="store_true", help="every registered document with a Docling cache")
    ap.add_argument("--out", required=True, type=Path, help="export root; each document goes to OUT/<name>/")
    ap.add_argument("--file-tokens", type=int, default=build.FILE_TOKENS, help="about this many tokens per text file")
    ap.add_argument("--table-file-tokens", type=int, default=None,
                    help="tables over this many tokens go to tables/<addr>.md (default: all tables stay in the text)")
    ap.add_argument("--width", type=int, default=fold.WIDTH, help="longest line, in characters")
    ap.add_argument("--as", dest="folder", help="folder name for a single document (default: its name); "
                    "a short name keeps each Grep hit line short")
    a = ap.parse_args(argv)
    names = [d.name for d in registry.docs()] if a.all else a.docs
    if not names:
        ap.error("name documents or use --all")
    if a.folder and len(names) != 1:
        ap.error("--as needs exactly one document")
    if a.folder and (Path(a.folder).name != a.folder or a.folder.startswith(".")):
        ap.error("--as takes a plain folder name")
    for name in names:
        try:
            registry.get(name)
        except KeyError:
            ap.error(f"unknown document {name!r}")
    for name in names:
        dest = a.out / (a.folder or name)
        if dest.exists() and not write.is_export(dest):
            ap.error(f"{dest} exists and is not an export; choose another --out or --as")
    for name in names:
        meta = write.export_doc(name, a.out, a.file_tokens, a.table_file_tokens, a.width, a.folder)
        print(f"{name}: {len(meta['files'])} text files, {meta['tokens']:,} tokens -> {a.out / (a.folder or name)}")
    readme = write.write_readme(a.out)
    print(f"index of the export: {readme}" if readme else f"{a.out / 'README.md'} is not ours: left unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
