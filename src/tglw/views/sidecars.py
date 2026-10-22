"""Load the delivered sidecars of tasks A, B, C1, C2, D1, D2 for one document.

Only delivered versions are read: a task's data/<ID>/MANIFEST.json must exist, and wip/ or
archived v*/ directories are never touched. A missing sidecar is simply absent (None).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from tglw.common.paths import DATA

SUFFIXES = {
    "A": ".structure.json",
    "B": ".pagemap.json",
    "B_toc": ".toc.json",
    "C1": ".terms.json",
    "C2": ".xrefs.json",
    "D1": ".textloss.json",
    "D2": ".tables.json",
    "D1_patch": ".patch.json",
}


@dataclass(frozen=True)
class Sidecars:
    structure: dict[str, Any] | None
    pagemap: dict[str, Any] | None
    toc: dict[str, Any] | None
    terms: dict[str, Any] | None
    xrefs: dict[str, Any] | None
    textloss: dict[str, Any] | None
    tables: dict[str, Any] | None
    patch: dict[str, Any] | None


@lru_cache(maxsize=None)
def delivered(task: str) -> bool:
    return (DATA / task / "MANIFEST.json").exists()


def manifest(task: str) -> dict[str, Any]:
    return json.loads((DATA / task / "MANIFEST.json").read_text())


def _load(key: str, name: str) -> dict[str, Any] | None:
    task = key.split("_")[0]
    path = DATA / task / f"{name}{SUFFIXES[key]}"
    if not delivered(task) or not path.exists():
        return None
    return json.loads(path.read_text())


def load(name: str) -> Sidecars:
    return Sidecars(*(_load(k, name) for k in SUFFIXES))


def extra(task: str, suffix: str, name: str) -> dict[str, Any] | None:
    """A delivered sidecar the views read only when a switch asks for it (task D1 v3's .boxfix.json)."""
    path = DATA / task / f"{name}{suffix}"
    return json.loads(path.read_text()) if delivered(task) and path.exists() else None


def versions() -> dict[str, str]:
    """Delivered version of each input task, for provenance."""
    out = {}
    for task in sorted({k.split("_")[0] for k in SUFFIXES}):
        if delivered(task):
            m = manifest(task)
            ver = m.get("version") or m.get("producer")
            out[task] = f"v{ver}" if isinstance(ver, int) else str(ver)
    return out
