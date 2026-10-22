import ast
import re
import subprocess
import sys
import tomllib
from importlib import import_module
from pathlib import Path

import tglw

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
IMPORT_NAMES = {"PIL": "pillow", "rank_bm25": "rank-bm25"}


def dist(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def test_console_scripts_point_at_callables():
    for name, target in PYPROJECT["project"]["scripts"].items():
        module, func = target.split(":")
        assert callable(getattr(import_module(module), func)), name


def test_identity_kept_in_one_place():
    assert PYPROJECT["project"]["name"] == tglw.PACKAGE
    assert PYPROJECT["project"]["urls"]["Repository"] == tglw.REPO
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "tglw.VERSION"}
    assert "version" in PYPROJECT["project"]["dynamic"] and tglw.NAME and tglw.VERSION


def test_dependencies_cover_third_party_imports():
    """Every third-party import under tglw is a declared dependency (core or an extra)."""
    declared = {dist(r) for r in PYPROJECT["project"]["dependencies"]}
    declared |= {dist(r) for reqs in PYPROJECT["project"].get("optional-dependencies", {}).values() for r in reqs}
    used = set()
    for path in Path(tglw.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else []
            used |= {n.split(".")[0] for n in names}
    third = {n for n in used if n not in sys.stdlib_module_names and n not in ("tglw", "__future__")}
    assert {IMPORT_NAMES.get(n, n).lower().replace("_", "-") for n in third} <= declared


def test_mit_license():
    assert PYPROJECT["project"]["license"] == "MIT"
    assert (ROOT / "LICENSE").read_text().startswith("MIT License")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tglw.cli", *args], capture_output=True, text=True)


def test_one_command_with_subcommands():
    assert PYPROJECT["project"]["scripts"] == {tglw.COMMAND: "tglw.cli:main"}
    shown = run_cli("--help")
    assert shown.returncode == 0
    assert [line.split()[0] for line in shown.stdout.splitlines() if line.startswith("  ")] == \
        ["ingest", "mcp", "export", "pipeline", "eval"]
    assert run_cli("--version").stdout == f"{tglw.COMMAND} {tglw.VERSION}\n"
    assert run_cli("no-such-command").returncode == 2
