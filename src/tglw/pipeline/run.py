"""Build all sidecars into a scratch root, then replace the output directory as a whole."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tglw.common import registry
from tglw.common.paths import DATA, WORK

from .steps import ORDER, SUFFIXES, now, write_json

PIPELINE = "tglw.pipeline v1"
HASH_SEED = "0"  # the page-label run context (tglw.pagemap) breaks ties by set order: pin it
DEFAULT_OUT = DATA / "I" / "wip" / "build"
MIRRORED = tuple(SUFFIXES) + ("M",)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        try:
            proc = subprocess.run(["git", "-C", str(WORK), *args], capture_output=True, text=True)
        except OSError:  # no git on this machine
            return ""
        return proc.stdout.rstrip("\n")

    dirty = [line[3:] for line in git("status", "--porcelain", "--", "src").splitlines()]
    return {"git_head": git("rev-parse", "--short", "HEAD"), "uncommitted_src": dirty}


def mirror(root: Path) -> None:
    """Copy the delivered top-level files of each task, so a subset build can read the other documents
    (C2 needs NIST 800-53r5's control index whatever document it is building)."""
    for task in MIRRORED:
        src = DATA / task
        for p in src.iterdir() if src.exists() else []:
            if p.is_file():
                (root / task).mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, root / task / p.name)


def inputs(names: list[str]) -> dict[str, Any]:
    out = {}
    for name in names:
        d = registry.get(name)
        out[name] = {"pdf_sha256": d.sha256, "docling_document_sha256": sha256(d.bundle_dir / "document.json")}
    return out


def sidecar_summary(root: Path) -> dict[str, Any]:
    out = {}
    for task in MIRRORED:
        m = root / task / "MANIFEST.json"
        man = json.loads(m.read_text()) if m.exists() else {}
        files = [p for s in SUFFIXES.get(task, ()) for p in (root / task).glob(f"*{s}")]
        producers = sorted({json.loads(p.read_text()).get("producer", "?") for p in files})
        out[task] = {"files": len(files), "producers": producers, "manifest_version": man.get("version")}
        if task == "M" and (root / "M" / "doc_index.json").exists():
            out[task]["documents"] = len(json.loads((root / "M" / "doc_index.json").read_text()))
    return out


def build(names: list[str], out: Path = DEFAULT_OUT) -> dict[str, Any]:
    everything = not names or set(names) == {d.name for d in registry.docs()}
    names = [d.name for d in registry.docs()] if everything else names
    tmp = out.with_name(f"{out.name}.tmp-{os.getpid()}")
    root = tmp / "data"
    root.mkdir(parents=True)
    if not everything:
        mirror(root)
    steps = []
    for task in ORDER:
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-m", "tglw.pipeline.worker", str(root), task, *names],
                              cwd=str(WORK), capture_output=True, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": HASH_SEED})
        steps.append({"task": task, "seconds": round(time.time() - t0, 1), "returncode": proc.returncode})
        (tmp / "logs").mkdir(exist_ok=True)
        (tmp / "logs" / f"{task}.log").write_text(proc.stdout + proc.stderr)
        print(f"{task:3s} {steps[-1]['seconds']:7.1f}s rc={proc.returncode}", flush=True)
        if proc.returncode:
            raise SystemExit(f"step {task} failed; scratch kept at {tmp} (see logs/{task}.log)")
    man = {"pipeline": PIPELINE, "built_at": now(), "mode": "all" if everything else "subset",
           "docs": names, "data_root": "data/", "steps": steps, "code": code_state(),
           "python": {"executable": sys.executable, "version": sys.version.split()[0], "hash_seed": HASH_SEED},
           "sidecars": sidecar_summary(root), "inputs": inputs(names),
           "note": "subset builds start from a copy of the delivered sidecars; only the listed documents are rebuilt"}
    write_json(tmp / "MANIFEST.json", man)
    if out.exists():
        old = out.with_name(f"{out.name}.old-{os.getpid()}")
        os.replace(out, old)
        os.replace(tmp, out)
        shutil.rmtree(old)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, out)
    return man
