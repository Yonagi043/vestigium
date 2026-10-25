"""Build a document library (tglw.HOME_ENV) holding the bundled sample, with its sidecars built.

tglw.common.paths fixes every location at import, so code that reads such a home runs in a child
process started with clean_env(home).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tglw import HOME_ENV

SAMPLE = "gao-26-107790"
RULES_SWITCH = "TGLW_RULES"  # the rule executor's switch; 0 = off (tglw.trace rules, executed in views)


def clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TGL_", "TGLW_", "VESTIGIUM_"))}
    keep = {RULES_SWITCH: os.environ[RULES_SWITCH]} if RULES_SWITCH in os.environ else {}
    return {**env, **keep, HOME_ENV: str(home), "PYTHONDONTWRITEBYTECODE": "1"}


def run(code: str, env: dict[str, str]) -> str:
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return proc.stdout


def build_sample_home(home: Path) -> Path:
    """Add the sample to an empty library the way a user would: `<command> ingest --sample` (no Docling needed)."""
    proc = subprocess.run([sys.executable, "-m", "tglw.ingest", "--sample"], env=clean_env(home), capture_output=True,
                          text=True, cwd=str(home))
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-3000:]
    return home


def registry_names(env: dict[str, str]) -> list[str]:
    return json.loads(run("import json; from tglw.common import registry; "
                          "print(json.dumps([d.name for d in registry.docs()]))", env))
