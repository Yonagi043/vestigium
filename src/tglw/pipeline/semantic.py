"""Semantic content of sidecars: what a sidecar says, without who wrote it or when.

A sidecar's semantic hash drops its top-level metadata (producer, version, inputs, timestamps) and
masks ISO timestamps inside strings, then hashes the canonical JSON. Two versions of a task that
differ only in such metadata (task B v4 renamed its producer, C2 v3 only re-recorded its inputs)
have the same semantic hash, so a consumer built on the older one is not out of date.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .steps import SUFFIXES

META_KEYS = frozenset({"producer", "producers", "version", "inputs", "written_at", "written", "time",
                       "updated", "created", "mtime", "built_at"})
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?")


def _mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _mask(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    if isinstance(obj, str):
        return _ISO.sub("<time>", obj)
    return obj


def semantic(obj: dict[str, Any]) -> Any:
    return _mask({k: v for k, v in obj.items() if k not in META_KEYS})


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=None)
def _file_hash(path: str, mtime: float, size: int) -> str:
    return _digest(semantic(json.loads(Path(path).read_text())))


def file_hash(path: Path) -> str:
    st = path.stat()
    return _file_hash(str(path), st.st_mtime, st.st_size)


def sidecars(directory: Path, task: str, part: str | None = None) -> dict[str, Path]:
    """Sidecar files directly in a task directory (or an archive of it), by file name."""
    suffixes = SUFFIXES.get(task, ())
    if part is not None:
        suffixes = tuple(s for s in suffixes if s.startswith(f".{part}."))
    return {p.name: p for s in suffixes for p in directory.glob(f"*{s}")}


def versions(root: Path, task: str) -> list[tuple[dict[str, Any], Path]]:
    """The delivered directory and every archive under it that carries a MANIFEST, newest first."""
    top = root / task
    dirs = [top] + sorted((d for d in top.iterdir() if d.is_dir() and (d / "MANIFEST.json").exists()),
                          key=lambda d: d.stat().st_mtime, reverse=True) if top.exists() else []
    return [(json.loads((d / "MANIFEST.json").read_text()), d) for d in dirs if (d / "MANIFEST.json").exists()]


def same_version(a: Any, b: Any) -> bool:
    return str(a).lstrip("v") == str(b).lstrip("v")


def changed_files(old: Path, new: Path, task: str, part: str | None = None) -> list[str] | None:
    """File names whose semantic content differs between two directories of one task (also files
    present in only one). None if the old directory holds no sidecars to compare."""
    a, b = sidecars(old, task, part), sidecars(new, task, part)
    if not a:
        return None
    return sorted(n for n in set(a) | set(b) if n not in a or n not in b or file_hash(a[n]) != file_hash(b[n]))


def find_file(root: Path, task: str, name: str, sha1_12: str) -> Path | None:
    """An earlier copy of a sidecar whose raw sha1 starts with the recorded 12 characters."""
    for _, d in versions(root, task):
        p = d / name
        if p.exists() and hashlib.sha1(p.read_bytes()).hexdigest()[:12] == sha1_12:
            return p
    return None
