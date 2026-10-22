"""Compare a pipeline build with the delivered sidecars, file by file.

Timestamps (keys such as written_at, time, mtime, and ISO times inside strings) are masked first.
Files whose remaining differences sit only in metadata (producer, inputs; see semantic.py) are
reported as "same apart from metadata"; anything else is "differs", with the JSON paths.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tglw.common import registry
from tglw.common.paths import DATA

from .semantic import semantic
from .steps import SUFFIXES

VOLATILE_KEYS = {"written_at", "written", "time", "updated", "created", "mtime", "built_at"}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2}|Z)?")


def mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<time>" if k in VOLATILE_KEYS else mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(v) for v in obj]
    if isinstance(obj, str):
        return _ISO.sub("<time>", obj)
    return obj


def diff_paths(a: Any, b: Any, path: str = "$", out: list[str] | None = None, limit: int = 8) -> list[str]:
    out = [] if out is None else out
    if len(out) >= limit or a == b:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k} ({'only in build' if k in b else 'only delivered'})")
            else:
                diff_paths(a[k], b[k], f"{path}.{k}", out, limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path} (length {len(a)} delivered vs {len(b)} build)")
        for i, (x, y) in enumerate(zip(a, b)):
            diff_paths(x, y, f"{path}[{i}]", out, limit)
            if len(out) >= limit:
                break
    else:
        out.append(f"{path}: {str(a)[:60]!r} -> {str(b)[:60]!r}")
    return out


def compare_file(delivered: Path, built: Path) -> dict[str, Any]:
    if not built.exists() or not delivered.exists():
        return {"status": "missing in build" if not built.exists() else "missing delivered"}
    a, b = json.loads(delivered.read_text()), json.loads(built.read_text())
    if a == b:
        return {"status": "identical"}
    ma, mb = mask(a), mask(b)
    if ma == mb:
        return {"status": "same but timestamps"}
    if semantic(a) == semantic(b):
        return {"status": "same apart from metadata", "paths": diff_paths(ma, mb)}
    return {"status": "differs", "paths": diff_paths(ma, mb)}


def main(run_dir: Path, json_out: Path | None = None) -> None:
    root = run_dir / "data"
    rows = []
    for task, suffixes in SUFFIXES.items():
        for suffix in suffixes:
            for d in registry.docs():
                r = compare_file(DATA / task / f"{d.name}{suffix}", root / task / f"{d.name}{suffix}")
                rows.append({"task": task, "suffix": suffix, "doc": d.name, **r})
    for name in ("doc_index.json",):
        r = compare_file(DATA / "M" / name, root / "M" / name)
        rows.append({"task": "M", "suffix": name, "doc": "*", **r})
    by: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for r in rows:
        by[(r["task"], r["suffix"])][r["status"]] += 1
    for (task, suffix), c in by.items():
        print(f"{task:3s} {suffix:16s} " + "  ".join(f"{k}: {v}" for k, v in sorted(c.items())))
    for r in rows:
        if r["status"] not in ("identical", "same but timestamps", "same apart from metadata"):
            print(f"  {r['task']:3s} {r['doc']:55s} {r['status']}: {'; '.join(r.get('paths', []))[:300]}")
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
