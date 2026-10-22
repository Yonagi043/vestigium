"""Pipeline steps, run inside a worker whose DATA points at a scratch root.

Each step calls the task's own generation function for one document (not its command line, whose
defaults the tasks are changing) and writes the result to <root>/<task>/wip/ itself. The step then
moves those files up into <root>/<task>/ and writes the task's MANIFEST, so the next step reads them
as delivered. Documents run in parallel in forked processes, which inherit the patched DATA.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable

import tglw.common.paths as paths
from tglw.common import registry

REAL = paths.WORK / "data"
ORDER = ("B", "A", "C1", "C2", "D1", "D2", "M")
JOBS = int(os.environ.get("TGLW_PIPELINE_JOBS", "12"))
SUFFIXES = {
    "A": (".structure.json",),
    "B": (".pagemap.json", ".toc.json"),
    "C1": (".terms.json",),
    "C2": (".xrefs.json",),
    "D1": (".textloss.json", ".patch.json"),
    "D2": (".tables.json",),
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def real_manifest(task: str) -> dict[str, Any]:
    path = REAL / task / "MANIFEST.json"
    return json.loads(path.read_text()) if path.exists() else {}


def code_version(producers: list[str]) -> str | None:
    """A task's version where no delivered MANIFEST names one (a product library): its newest
    producer's, e.g. ["tglw.textloss v1", "tglw.textloss.patch v2"] -> "v2"."""
    vs = [p.rsplit(" ", 1)[-1] for p in producers if " v" in p]
    return max(vs, key=lambda v: tuple(int(x) for x in v[1:].split(".") if x.isdigit()), default=None)


def doc_of(file: str) -> str:
    return next((file[: -len(s)] for ss in SUFFIXES.values() for s in ss if file.endswith(s)), file)


def wip(task: str) -> Path:
    return paths.DATA / task / "wip"


def promote(task: str) -> list[str]:
    """Move generated files from <root>/<task>/wip/ up into <root>/<task>/ (atomic renames)."""
    src, dest = wip(task), paths.DATA / task
    moved = []
    for p in sorted(src.rglob("*")):
        if p.is_file() and not p.name.endswith(".tmp"):
            target = dest / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(p, target)
            moved.append(str(p.relative_to(src)))
    shutil.rmtree(src, ignore_errors=True)
    return moved


def manifest(task: str, **extra: Any) -> dict[str, Any]:
    """MANIFEST for a task without its own writer: the delivered version, producers and files found."""
    dest = paths.DATA / task
    files = sorted(p.name for s in SUFFIXES.get(task, ()) for p in dest.glob(f"*{s}"))
    producers = sorted({json.loads((dest / f).read_text()).get("producer", "?") for f in files})
    m = {"task": task, "version": real_manifest(task).get("version") or code_version(producers), "producers": producers,
         "docs": len({doc_of(f) for f in files}), "files": files, "written_at": now(),
         "built_by": "tglw.pipeline", **extra}
    write_json(dest / "MANIFEST.json", m)
    return m


def each(fn: Callable[[str], None], names: list[str]) -> None:
    """Run fn over documents in forked processes; any failure fails the step."""
    if len(names) == 1 or JOBS <= 1:
        for name in names:
            fn(name)
        return
    with ProcessPoolExecutor(min(JOBS, len(names)), mp_context=multiprocessing.get_context("fork")) as pool:
        list(pool.map(fn, names))


def _b_pagemap(name: str) -> None:
    from tglw.pagemap import build as pb

    write_json(wip("B") / f"{name}.pagemap.json", pb.build(name))


def _b_toc(name: str) -> None:
    from tglw.pagemap import build_toc as bt

    write_json(wip("B") / f"{name}.toc.json", bt.build_toc(name))


def step_b(names: list[str]) -> None:
    from tglw.pagemap import build as pb
    from tglw.pagemap import build_toc as bt
    from tglw.pagemap import manifest as bm

    assert pb.WIP_DIR == wip("B"), "build_toc reads pagemaps from B's wip/"
    each(_b_pagemap, names)
    each(_b_toc, names)
    promote("B")
    bm.write(real_manifest("B").get("version") or int(code_version([pb.PRODUCER, bt.PRODUCER])[1:].split(".")[0]))


def _a(name: str) -> None:
    from tglw.structure import build as sb

    write_json(wip("A") / f"{name}.structure.json", sb.build(name))


def step_a(names: list[str]) -> None:
    from tglw.common import pdfmeta
    from tglw.structure import pdfoutline

    have = json.loads(pdfoutline.CACHE.read_text()) if pdfoutline.CACHE.exists() else None
    if have is not None and any(n not in have and pdfmeta.outline(n) for n in names):
        pdfoutline.build_cache()  # a newly added document: its bookmarks are not in the mirrored cache yet
    pdfoutline.outline(names[0])
    each(_a, names)
    promote("A")
    b = json.loads((paths.DATA / "B" / "MANIFEST.json").read_text())
    manifest("A", inputs={"task_B": {k: v.get("producers") for k, v in b.get("parts", {}).items()}})


def _c1(name: str) -> None:
    from tglw.terms import build as tb

    write_json(wip("C1") / f"{name}.terms.json", tb.build_doc(name))


def step_c1(names: list[str]) -> None:
    from tglw.terms import build as tb

    each(_c1, names)
    promote("C1")
    tb.write_manifest([d.name for d in registry.docs()], out=paths.DATA / "C1")


_CONTROLS: Any = None


def _c2(name: str) -> None:
    from tglw.xrefs import build as xb

    write_json(wip("C2") / f"{name}.xrefs.json", xb.build(name, _CONTROLS))


def step_c2(names: list[str]) -> None:
    global _CONTROLS
    from tglw.common import bundle
    from tglw.xrefs import build as xb

    registered = {d.name for d in registry.docs()}
    # NIST controls in other NIST documents resolve against 800-53r5; a library without it leaves them unresolved
    _CONTROLS = xb.build_index(bundle.load(xb.CONTROL_DOC), controls=True) if xb.CONTROL_DOC in registered else None
    each(_c2, names)
    promote("C2")
    manifest("C2", inputs=xb.inputs())


def _d1(name: str) -> None:
    from tglw.textloss import patch as tp
    from tglw.textloss.check import check_doc

    write_json(wip("D1") / f"{name}.textloss.json", check_doc(name))
    write_json(wip("D1") / f"{name}.patch.json", tp.build(name))


def step_d1(names: list[str]) -> None:
    each(_d1, names)
    promote("D1")
    manifest("D1")


def _d2(name: str) -> None:
    from tglw.tables import build as td

    write_json(wip("D2") / f"{name}.tables.json", td.doc_json(name))


def step_d2(names: list[str]) -> None:
    each(_d2, names)
    promote("D2")
    manifest("D2")


def step_m(names: list[str]) -> None:
    from tglw.mcp_server import index as mi

    rows = mi.build()
    write_json(paths.DATA / "M" / "doc_index.json", rows)
    write_json(paths.DATA / "M" / "MANIFEST.json", {
        "task": "M", "version": real_manifest("M").get("version"), "files": ["doc_index.json"],
        "docs": len(rows), "written_at": now(), "built_by": "tglw.pipeline"})


STEPS: dict[str, Callable[[list[str]], None]] = {
    "B": step_b, "A": step_a, "C1": step_c1, "C2": step_c2, "D1": step_d1, "D2": step_d2, "M": step_m,
}
