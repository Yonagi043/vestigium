"""Start the MCP server over stdio against a document library and call each of its six tools once.

python tests/pkg/mcp_smoke.py --home DIR [--doc NAME] [--command "vestigium mcp"]   # prints each call and result
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tglw import COMMAND, HOME_ENV

TOOLS = ("list_documents", "outline", "read", "context", "search", "region")


def server(home: Path, command: list[str]) -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TGL_", "TGLW_", "VESTIGIUM_"))
           or k == "TGLW_RULES"}  # the caller decides whether rules run
    home = home.resolve()
    env.update({HOME_ENV: str(home), "PYTHONDONTWRITEBYTECODE": "1"})
    return StdioServerParameters(command=command[0], args=command[1:], cwd=str(home), env=env)


def text_of(result: Any) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def images_of(result: Any) -> int:
    return sum(1 for c in result.content if getattr(c, "type", "") == "image")


async def calls(home: Path, doc: str, command: list[str]) -> list[dict[str, Any]]:
    """One call per tool, each argument taken from an earlier result where it can be."""
    out: list[dict[str, Any]] = []

    async def call(tool: str, args: dict[str, Any]) -> str:
        r = await client.call_tool(tool, args)
        out.append({"tool": tool, "args": args, "is_error": bool(r.is_error), "text": text_of(r), "images": images_of(r)})
        return out[-1]["text"]

    async with Client(server(home, command)) as client:
        names = sorted(t.name for t in (await client.list_tools()).tools)
        await call("list_documents", {})
        outline = await call("outline", {"doc": doc})
        section = re.search(r"^(s\d+) .* · (?:\d+(?:\.\d)?k|[1-9]\d{2,})$", outline, re.M)  # first with 100+ tokens
        await call("read", {"doc": doc, "addrs": [section.group(1) if section else "p1"]})
        found = await call("search", {"doc": doc, "query": "FEMA"})
        addr = re.search(r"\b(t\d+)\b", found)
        await call("context", {"doc": doc, "addr": addr.group(1) if addr else "p1"})
        await call("region", {"doc": doc, "target": "p1"})
    out.insert(0, {"tool": "list_tools", "names": names})
    return out


def default_command() -> list[str]:
    exe = shutil.which(COMMAND, path=str(Path(sys.executable).parent))
    return [exe, "mcp"] if exe else [sys.executable, "-m", "tglw.mcp_server"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--doc", default="gao-26-107790")
    ap.add_argument("--command", default=None, help="server command line (default: `<command> mcp` next to this python)")
    args = ap.parse_args()
    for c in asyncio.run(calls(args.home, args.doc, shlex.split(args.command) if args.command else default_command())):
        if c["tool"] == "list_tools":
            print("## tools:", ", ".join(c["names"]))
            continue
        print(f"## {c['tool']}({c['args']}) error={c['is_error']} images={c['images']}")
        print(c["text"])


if __name__ == "__main__":
    main()
