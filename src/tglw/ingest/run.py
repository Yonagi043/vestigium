"""The ingest command: add PDFs to the library, or remove them.

add, per PDF: register (name defaults to the file name) -> Docling bundle (reused when the cache
already holds this PDF's sha256) -> sidecars for this document only (tglw.pipeline subset build,
then copied into DATA) -> MCP document index (part of that build) -> quality report (qc.py) ->
corpus index (tglw.corpus.update).

Docling runs in this process when it is installed here; otherwise in the interpreter named by
TGLW_INGEST_PYTHON (a separate environment with the [ingest] extra), with this package on PYTHONPATH.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tglw
from tglw import PACKAGE
from tglw.common import paths, registry
from tglw.pipeline import run as pipeline_run
from tglw.pipeline.steps import SUFFIXES

from . import convert, library, qc

PYTHON_ENV = "TGLW_INGEST_PYTHON"
TASKS = (*SUFFIXES, "M")


@dataclass
class Outcome:
    name: str
    pdf: str
    status: str = "ok"  # ok | exists | failed
    converted: bool = False
    pages: int = 0
    seconds: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    qc: dict[str, Any] | None = None

    def line(self) -> str:
        parts = [f"{self.status:6s} {self.name}"]
        if self.status != "failed":
            parts += [f"{self.pages} p", "converted" if self.converted else "cached bundle"]
        if self.qc:
            v = self.qc["verdict"]
            parts.append(f"qc {v['status']}" + (f" ({len(v['suspect_pages'])} suspect pages)" if v["suspect_pages"] else ""))
        parts += [f"{k} {v:.0f}s" for k, v in self.seconds.items()]
        return "\n".join([" · ".join(parts), *(f"       {n}" for n in self.notes)])


def docling_command() -> list[str] | None:
    """None: convert in this process. Else the interpreter command for tglw.ingest.convert: the
    TGLW_INGEST_PYTHON interpreter, or a research checkout's INGEST_PYTHON when Docling is not here."""
    python = os.environ.get(PYTHON_ENV)
    if not python and importlib.util.find_spec("docling"):
        return None
    python = python or getattr(paths._research, "INGEST_PYTHON", None)
    if not python:
        raise library.LibraryError(f"Docling is not installed: pip install '{PACKAGE}[ingest]', or point "
                                   f"{PYTHON_ENV} at a Python that has it")
    return [str(Path(python).expanduser()), "-m", "tglw.ingest.convert"]


def bundle_ok(entry: Path, sha: str, size: int) -> bool:
    try:
        man = json.loads((entry / "manifest.json").read_text())
    except (OSError, ValueError):
        return False
    return (man.get("schema") == paths.BUNDLE_SCHEMA and man.get("source_sha256") == sha
            and man.get("source_size") == size and (entry / "document.json").exists() and (entry / "pages.json").exists())


def ensure_bundle(pdf: Path, sha: str, ocr: str, page_images: bool, device: str, force: bool,
                  scan_text_ocr: str = "fill") -> tuple[bool, int]:
    """(converted, pages): reuse a sound cache entry, else run Docling."""
    entry = paths.CACHE_DIR / sha
    if bundle_ok(entry, sha, pdf.stat().st_size) and not force:
        return False, json.loads((entry / "manifest.json").read_text())["page_count"]
    if entry.exists() and not force:
        raise library.LibraryError(f"cache entry {entry} is incomplete or for another file; rerun with --force")
    cache_root = paths.CACHE_DIR.parent
    cmd = docling_command()
    if cmd is None:
        convert.run(pdf, cache_root, ocr, page_images=page_images, device=device, force=force,
                    scan_text_ocr=scan_text_ocr)
    else:
        args = [*cmd, str(pdf), "--cache-root", str(cache_root), "--ocr", ocr, "--device", device,
                "--scan-text-ocr", scan_text_ocr]
        args += ["--page-images"] * page_images + ["--force"] * force
        src = str(Path(tglw.__file__).resolve().parents[1])
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([src, os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(args, env=env, capture_output=True, text=True)
        log = paths.DATA / "ING" / "logs" / f"{sha[:12]}.convert.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(proc.stdout + proc.stderr)
        if proc.returncode:
            last = (proc.stderr.strip().splitlines() or ["(no message)"])[-1]
            raise RuntimeError(f"Docling conversion failed: {last} (full log: {log})")
    if not bundle_ok(entry, sha, pdf.stat().st_size):
        raise RuntimeError(f"conversion did not leave a complete bundle at {entry}")
    return True, json.loads((entry / "manifest.json").read_text())["page_count"]


def deliver(build_root: Path, name: str) -> None:
    """Copy this document's sidecars and the rewritten task MANIFESTs (and M's index) into DATA."""
    for task in TASKS:
        src, dest = build_root / task, paths.DATA / task
        if not src.is_dir():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        files = [p for p in src.iterdir() if p.is_file() and (p.name.startswith(f"{name}.") or task == "M")]
        files += [p for p in (src / "outline_xyz.json", src / "MANIFEST.json") if p.exists() and task != "M"]
        for p in files:
            tmp = dest / f".{p.name}.tmp-{os.getpid()}"
            shutil.copy2(p, tmp)
            os.replace(tmp, dest / p.name)


def sidecars(name: str) -> float:
    t0 = time.time()
    out = paths.DATA / "ING" / "wip" / f"build-{name}-{os.getpid()}"
    pipeline_run.build([name], out)
    deliver(out / "data", name)
    shutil.rmtree(out, ignore_errors=True)
    if not any(out.parent.iterdir()):
        out.parent.rmdir()
    return time.time() - t0


def update_corpus(name: str, drop: bool = False) -> str | None:
    """None when done; else why the corpus index was not updated."""
    try:
        from tglw import corpus
    except ImportError:
        return "corpus index not available in this version (tglw.corpus missing)"
    try:
        corpus.drop(name) if drop else corpus.update(name)
    except Exception as err:  # noqa: BLE001 - the library stays usable without the corpus index
        return f"corpus index not updated ({type(err).__name__}: {err}); run python -m tglw.corpus build --stale"
    return None


def add_one(pdf: Path, name: str | None, cls: str, ocr: str, page_images: bool, device: str, force: bool,
            scan_text_ocr: str = "fill") -> Outcome:
    library.check_writable()
    pdf = pdf.expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise library.LibraryError(f"not a PDF file: {pdf}")
    sha = convert.sha256(pdf)
    same = library.by_sha(sha)
    if name is not None:
        library.valid_name(name)
        other = next((d for d in registry.docs(cached_only=False) if d.name == name), None)
        if other is not None and other.sha256 != sha:
            raise library.LibraryError(f"{name!r} is already a different PDF in the library ({other.pdf})")
    if same is not None and not force:
        out = Outcome(same.name, str(pdf), status="exists", pages=same.pages)
        out.notes.append(f"already in the library as {same.name} (use --force to convert and rebuild again)")
        return out
    name = name or (same.name if same else library.free_name(library.slug(pdf.stem)))
    out = Outcome(name, str(pdf))
    t0 = time.time()
    rel = library.copy_pdf(pdf, name)
    try:
        out.converted, out.pages = ensure_bundle(paths.DOC_DIR / rel, sha, ocr, page_images, device, force, scan_text_ocr)
    except BaseException:
        if same is None:  # nothing registered refers to the copy yet
            (paths.DOC_DIR / rel).unlink(missing_ok=True)
        raise
    out.seconds["docling"] = time.time() - t0
    registry.put(library.info(name, cls, rel, sha, out.pages, True, pdf.name))
    library.put_pdf_meta(library.pdf_meta_row(name, paths.DOC_DIR / rel))
    out.seconds["sidecars"] = sidecars(name)
    t0 = time.time()
    out.qc = qc.write(name)
    out.seconds["qc"] = time.time() - t0
    note = update_corpus(name)
    if note:
        out.notes.append(note)
    registry.touch()
    return out


def add(pdfs: list[Path], name: str | None = None, cls: str = library.DEFAULT_CLS, ocr: str = "rapidocr",
        page_images: bool = False, device: str = "auto", force: bool = False, scan_text_ocr: str = "fill",
        ) -> list[Outcome]:
    if name is not None and len(pdfs) != 1:
        raise library.LibraryError("--name works with one PDF at a time")
    outcomes = []
    for pdf in pdfs:
        try:
            with library.locked():
                outcomes.append(add_one(pdf, name, cls, ocr, page_images, device, force, scan_text_ocr))
        except (Exception, SystemExit) as err:  # noqa: BLE001 - one bad PDF must not stop the others
            note = str(err) if isinstance(err, (library.LibraryError, RuntimeError)) else f"{type(err).__name__}: {err}"
            outcomes.append(Outcome(name or Path(pdf).stem, str(pdf), status="failed", notes=[note]))
        print(outcomes[-1].line(), flush=True)
    return outcomes


def rebuild(names: list[str]) -> list[Outcome]:
    """Sidecars, index, report and corpus entry again from the cached bundles (no Docling), e.g.
    after the sidecar code changed."""
    library.check_writable()
    outcomes = []
    for name in names:
        out = Outcome(name, "")
        try:
            with library.locked():
                out.pages = registry.get(name).pages
                out.seconds["sidecars"] = sidecars(name)
                out.qc = qc.write(name)
                note = update_corpus(name)
                registry.touch()
            if note:
                out.notes.append(note)
        except (Exception, SystemExit) as err:  # noqa: BLE001 - report and go on with the next document
            out.status, out.notes = "failed", [f"{type(err).__name__}: {err}"]
        outcomes.append(out)
        print(out.line(), flush=True)
    return outcomes


def add_samples() -> list[Outcome]:
    """The bundled samples: their bundles go into the cache first, so Docling is not needed."""
    from tglw import samples

    library.check_writable()
    outcomes = []
    for name in samples.NAMES:
        if not bundle_ok(paths.CACHE_DIR / samples.sha256(name), samples.sha256(name), samples.pdf(name).stat().st_size):
            samples.install_bundle(name, paths.CACHE_DIR)
        outcomes += add([samples.pdf(name)], name=name, cls=getattr(samples, "CLASSES", {}).get(name, library.DEFAULT_CLS))
    return outcomes


def remove(name: str, keep_cache: bool = False) -> list[str]:
    """Drop a document from the registry, its sidecars, report and PDF copy; rebuild M's index."""
    library.check_writable()
    with library.locked():
        return _remove(name, keep_cache)


def _remove(name: str, keep_cache: bool) -> list[str]:
    info = registry.remove(name)
    library.drop_pdf_meta(name)
    gone = []
    for task in (*TASKS, "ING"):
        for p in (paths.DATA / task).glob(f"{name}.*") if (paths.DATA / task).is_dir() else []:
            p.unlink()
            gone.append(str(p))
    pdf = paths.DOC_DIR / info.pdf
    if pdf.exists() and pdf.parent == paths.DOC_DIR:
        pdf.unlink()
        gone.append(str(pdf))
    if not keep_cache and library.by_sha(info.sha256) is None and info.bundle_dir.is_dir():
        shutil.rmtree(info.bundle_dir)
        gone.append(str(info.bundle_dir))
    _rebuild_index()
    note = update_corpus(name, drop=True)
    registry.touch()
    return gone + ([note] if note else [])


def _rebuild_index() -> None:
    from tglw.pipeline import steps

    steps.step_m([])
