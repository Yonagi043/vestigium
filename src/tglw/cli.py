"""The console command (tglw.COMMAND): one entry point whose subcommands run the package's tools.

Each subcommand's module has main(argv) taking the arguments after the subcommand and returning an exit
status; its parser's prog is f"{COMMAND} <subcommand>". `python -m tglw.cli` works the same way.
"""

from __future__ import annotations

import importlib
import inspect
import sys

from tglw import COMMAND, VERSION

SUBCOMMANDS = {
    "ingest": ("tglw.ingest.__main__", "add PDFs to the document library; list, remove, rebuild, quality reports"),
    "mcp": ("tglw.mcp_server.server", "run the MCP server (stdio, or --http for a resident server)"),
    "export": ("tglw.export.cli", "write a document as plain files, for agents without MCP"),
    "pipeline": ("tglw.pipeline.__main__", "rebuild, check or compare a library's sidecars"),
    "eval": ("tglw.score.__main__", "plan, run and score evaluations (needs the claude CLI)"),
}


def usage() -> str:
    width = max(map(len, SUBCOMMANDS))
    return "\n".join([f"usage: {COMMAND} <command> [args ...]", "", "commands:",
                      *(f"  {name:<{width}}  {what}" for name, (_, what) in SUBCOMMANDS.items()), "",
                      f"`{COMMAND} <command> --help` shows a command's options; `{COMMAND} --version` the version."])


def main(argv: list[str] | None = None) -> int | None:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print(usage())
        return 0
    if args[0] == "--version":
        print(f"{COMMAND} {VERSION}")
        return 0
    if args[0] not in SUBCOMMANDS:
        print(f"{COMMAND}: unknown command {args[0]!r}\n\n{usage()}", file=sys.stderr)
        return 2
    run = importlib.import_module(SUBCOMMANDS[args[0]][0]).main
    sys.argv = [f"{COMMAND} {args[0]}", *args[1:]]  # argparse's default prog, for a main() that reads sys.argv
    return run(args[1:]) if inspect.signature(run).parameters else run()


if __name__ == "__main__":
    raise SystemExit(main())
