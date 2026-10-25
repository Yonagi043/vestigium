"""Where PDFs, Docling caches and sidecar data live.

Product layout, under the library directory named by VESTIGIUM_HOME (tglw.HOME_ENV; TGL_HOME, its name
before v1, still works), default ~/.vestigium: docs/ (DOC_DIR, the registered PDFs),
cache/slottree-docling-bundle-v1/ (CACHE_DIR, one Docling bundle per PDF sha256) and data/ (DATA:
common/docs.json registry, one directory per sidecar task). WORK is that directory, so WORK/data is DATA
as in a research checkout.

A research checkout also carries _research_defaults.py (not released). Unless a library directory is set,
that checkout's locations are used instead, and DATA is data/ of the checkout (WORK).

Single locations can be moved either way: TGL_DOCS, TGL_CACHE, TGL_WORK, TGL_DATA. In the product layout,
tiktoken's vocabulary is cached in HOME/cache/tiktoken unless TIKTOKEN_CACHE_DIR (or DATA_GYM_CACHE_DIR) is set.

Reading traces and agent-written rules stay on this machine, under the library directory in both
layouts: trace_dir() is HOME/traces and rules_dir() HOME/rules, unless VESTIGIUM_TRACE_DIR or
VESTIGIUM_RULES_DIR (TRACE_ENV, RULES_ENV) name another directory, as an evaluation run does for its traces.
"""

from __future__ import annotations

import os
from pathlib import Path

from tglw import COMMAND, HOME_ENV

try:
    from . import _research_defaults as _research
except ImportError:
    _research = None


def _env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


BUNDLE_SCHEMA = "slottree-docling-bundle-v1"
_HOME_SET = os.environ.get(HOME_ENV) or os.environ.get("TGL_HOME")  # TGL_HOME: the name before v1
HOME = Path(_HOME_SET).expanduser() if _HOME_SET else Path.home() / f".{COMMAND}"
RESEARCH = _research is not None and not _HOME_SET

TGL: Path | None = _env("TGL_ROOT", _research.TGL_ROOT) if _research else None
RUN_DIR: Path | None = TGL / _research.RUNS if _research and TGL else None
SCRIPT_DIR: Path | None = TGL / _research.SCRIPTS if _research and TGL else None

if RESEARCH:
    DOC_DIR = _env("TGL_DOCS", TGL / _research.DOCS)
    CACHE_DIR = _env("TGL_CACHE", TGL / _research.CACHE)
    WORK = _env("TGL_WORK", Path(__file__).resolve().parents[3])
else:
    DOC_DIR = _env("TGL_DOCS", HOME / "docs")
    CACHE_DIR = _env("TGL_CACHE", HOME / "cache" / BUNDLE_SCHEMA)
    WORK = _env("TGL_WORK", HOME)
DATA = _env("TGL_DATA", WORK / "data")
COMMON_DATA = DATA / "common"
if not RESEARCH and not (os.environ.get("TIKTOKEN_CACHE_DIR") or os.environ.get("DATA_GYM_CACHE_DIR")):
    # token counts need tiktoken's vocabulary, fetched once; keep it in the library, not in a temp directory a
    # reboot may empty, so that after a first online run the server also starts offline
    os.environ["TIKTOKEN_CACHE_DIR"] = str(HOME / "cache" / "tiktoken")

TRACE_ENV = f"{HOME_ENV.removesuffix('_HOME')}_TRACE_DIR"
RULES_ENV = f"{HOME_ENV.removesuffix('_HOME')}_RULES_DIR"


def trace_dir() -> Path:
    """Where reading traces are written; the environment is read at each call. Not created here."""
    return _env(TRACE_ENV, HOME / "traces")


def rules_dir() -> Path:
    """Where agent-written rules are kept; the environment is read at each call. Not created here."""
    return _env(RULES_ENV, HOME / "rules")
