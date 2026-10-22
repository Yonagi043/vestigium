"""Check per document: every sidecar present, versions consistent across tasks, MCP tools can read it.

Severity: error (a sidecar or MANIFEST is missing, an MCP call fails), warning (a task was built on
upstream content that has since changed, or the change cannot be verified; a sidecar is newer than
its MANIFEST), info (worth knowing: e.g. an upstream version number moved but its semantic content,
see semantic.py, is the same).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from tglw.common import registry
from tglw.common.paths import DATA, WORK

from . import semantic
from .steps import SUFFIXES

QUERY = "introduction"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def _sha1_12(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _producers(man: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("producer", "producers"):
        v = man.get(key)
        out |= {v} if isinstance(v, str) else set(v or [])
    for part in (man.get("parts") or {}).values():
        out |= set(part.get("producers") or [])
    return out


def _drift(root: Path, consumer: str, task: str, old_dir: Path | None, what: str, part: str | None = None,
           doc: str | None = None) -> dict[str, Any]:
    """A consumer recorded an older upstream version: info if the content is the same apart from
    metadata, warning if it changed or cannot be verified (no archived copy of that version)."""
    base = {"task": consumer, "doc": doc}
    if old_dir is None:
        return {**base, "severity": "warning", "msg": f"{what}; no archived copy of that version to compare"}
    changed = semantic.changed_files(old_dir, root / task, task, part)
    if changed is None:
        return {**base, "severity": "warning", "msg": f"{what}; {old_dir} holds no {task} sidecars to compare"}
    if not changed:
        return {**base, "severity": "info", "msg": f"{what}; content identical apart from metadata"}
    return {**base, "severity": "warning",
            "msg": f"{what}; content changed in {len(changed)} files, e.g. {changed[:3]}"}


def _archive_by_producers(root: Path, task: str, recorded: dict[str, list[str]]) -> Path | None:
    for man, d in semantic.versions(root, task):
        parts = {k: v.get("producers") for k, v in (man.get("parts") or {}).items()}
        if all(parts.get(k) == v for k, v in recorded.items()):
            return d
    return None


def _archive_by_version(root: Path, task: str, version: Any) -> Path | None:
    return next((d for man, d in semantic.versions(root, task) if semantic.same_version(man.get("version"), version)),
                None)


def task_findings(root: Path) -> list[dict[str, Any]]:
    """Document-independent checks: MANIFESTs present, and whether the upstream versions each
    consumer recorded still say the same thing (semantic content), not merely carry the same number."""
    out = []
    mans = {t: _json(root / t / "MANIFEST.json") for t in (*SUFFIXES, "M", "F", "G")}
    for t in (*SUFFIXES, "M"):
        if not mans[t]:
            out.append({"severity": "error", "task": t, "doc": None, "msg": "MANIFEST.json missing"})
    b_parts = {k: v.get("producers") for k, v in (mans["B"].get("parts") or {}).items()}
    used = {k: v for k, v in (((mans["A"].get("inputs") or {}).get("task_B")) or {}).items() if k in b_parts}
    for part, producers in used.items():
        if producers != b_parts[part]:
            what = f"A was built on B {part} {producers}, B now delivers {b_parts[part]}"
            out.append(_drift(root, "A", "B", _archive_by_producers(root, "B", {part: producers}), what, part))
    for consumer in ("F", "G", "M"):
        inputs = mans[consumer].get("inputs")
        for t, v in (inputs.items() if isinstance(inputs, dict) else []):
            want = v.get("version") if isinstance(v, dict) else v
            have = mans.get(t, {}).get("version") if t in mans else None
            if have is None or semantic.same_version(want, have):
                continue
            what = f"{consumer} MANIFEST lists {t} {want}, {t} is now {have}"
            if t in SUFFIXES:
                out.append(_drift(root, consumer, t, _archive_by_version(root, t, want), what))
            else:
                out.append({"severity": "warning", "task": consumer, "doc": None,
                            "msg": f"{what} ({t} has no per-document sidecars to compare)"})
    return out


def doc_findings(root: Path, name: str) -> list[dict[str, Any]]:
    out = []

    def add(sev: str, task: str, msg: str) -> None:
        out.append({"severity": sev, "task": task, "doc": name, "msg": msg})

    for task, suffixes in SUFFIXES.items():
        man_path = root / task / "MANIFEST.json"
        man = _json(man_path)
        for suffix in suffixes:
            path = root / task / f"{name}{suffix}"
            if not path.exists():
                add("error", task, f"{name}{suffix} missing")
                continue
            producer = _json(path).get("producer")
            if man and producer not in _producers(man):
                add("warning", task, f"producer {producer!r} not listed in {task} MANIFEST {sorted(_producers(man))}")
            if man_path.exists() and path.stat().st_mtime > man_path.stat().st_mtime:
                add("warning", task, f"{name}{suffix} is newer than {task}/MANIFEST.json (task G ignores such a task)")
    xr = _json(root / "C2" / f"{name}.xrefs.json").get("inputs") or {}
    for task, suffix in (("A", "structure"), ("B", "pagemap")):
        rec = xr.get(f"{task}_file")
        path = root / task / f"{name}.{suffix}.json"
        if rec and path.exists() and rec.get("sha1") != _sha1_12(path):
            what = f"C2 was built on another {task} sidecar (sha1 {rec.get('sha1')}, now {_sha1_12(path)})"
            old = semantic.find_file(root, task, path.name, rec.get("sha1", ""))
            if old is None:
                add("warning", "C2", f"{what}; that copy is not archived, cannot compare content")
            elif semantic.file_hash(old) == semantic.file_hash(path):
                add("info", "C2", f"{what}; content identical apart from metadata")
            else:
                add("warning", "C2", f"{what}; content changed")
    b_version = _json(root / "B" / "MANIFEST.json").get("version")
    pages = xr.get("pages", "")
    if pages and b_version is not None and f"pagemap v{b_version}" not in pages:
        add("info", "C2", f"C2 recorded '{pages}', B MANIFEST is version {b_version} (the file check above "
                          "decides whether that matters)")
    side_a = _json(root / "A" / f"{name}.structure.json")
    status = (side_a.get("verdict") or {}).get("status")
    if status in ("unknown", "partial"):
        add("info", "A", f"section structure {status}")
    return out


def mcp_smoke(names: list[str]) -> list[dict[str, Any]]:
    """Call what the MCP tools call (list_documents, outline, read, search). Runs in a worker whose DATA
    is the data root under check."""
    from tglw import views
    from tglw.mcp_server import tools

    out = []
    for name in names:
        def fail(msg: str) -> None:
            out.append({"severity": "error", "task": "MCP", "doc": name, "msg": msg})

        try:
            if name not in tools.doc_index():
                fail("not in doc_index.json (list_documents cannot show it)")
            text = views.outline(name)
            if not text.strip():
                fail("outline is empty")
            if not views.read(name, ["p1"]).strip():
                fail("read p1 returned nothing")
            views.search(name, QUERY, k=3)
        except Exception as err:  # noqa: BLE001 - every failure is a finding
            fail(f"{type(err).__name__}: {err}")
    return out


def run_mcp(root: Path, names: list[str]) -> list[dict[str, Any]]:
    proc = subprocess.run([sys.executable, "-m", "tglw.pipeline.worker", str(root), "mcp", *names],
                          cwd=str(WORK), capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if proc.returncode:
        return [{"severity": "error", "task": "MCP", "doc": None, "msg": f"check worker failed: {proc.stderr[-400:]}"}]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(names: list[str], data_root: Path | None, mcp: bool = True, json_out: Path | None = None) -> None:
    root = data_root or DATA
    names = names or [d.name for d in registry.docs()]
    findings = task_findings(root)
    for name in names:
        findings += doc_findings(root, name)
    if mcp:
        findings += run_mcp(root, names)
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["task"] or "", f["doc"] or ""))
    counts = Counter(f["severity"] for f in findings)
    for f in findings:
        print(f"{f['severity']:7s} {f['task'] or '-':4s} {f['doc'] or '-':55s} {f['msg']}")
    print(f"\n{root}: {len(names)} documents; " + ", ".join(f"{k} {counts.get(k, 0)}" for k in order))
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps({"root": str(root), "docs": len(names), "counts": dict(counts),
                                        "findings": findings}, ensure_ascii=False, indent=1))
