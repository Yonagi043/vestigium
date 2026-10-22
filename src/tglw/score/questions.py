"""Questions to score, from the 6x10 set or task H's JSONL files, in one shape.

6x10 facets are all required and share the case's evidence pages. Task H facets carry their own
required flag and evidence. An evidence item may name another document ("doc"; without it the item
belongs to the question's own document): its pages are kept apart as (doc, page), so `pages` only
ever holds pages of the question's own document. Image evidence (task H4: {"page", "bbox", "kind":
"image", "what", "probes"}) gives its page as `page`; its pages count like text evidence, and `images`
keeps (page, what) for the sheet. Files inside a held-out directory (one whose name starts with a
prefix of `settings.HELDOUT`) hold a confirmation set and are refused unless the caller passes
`allow_frozen=True` (final acceptance only). The 6x10 set is research material: `load_all` includes
it only where its loader (tglw.score.sixten_set, not released) can be imported.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tglw.common import registry
from tglw.common.paths import DATA

from . import settings

H_DIR = DATA / "H"


@dataclass(frozen=True, slots=True)
class Facet:
    id: str
    text: str
    required: bool = True
    pages: tuple[int, ...] = ()
    printed: tuple[str, ...] = ()
    elsewhere: tuple[tuple[str, int], ...] = ()  # (doc, pdf page) of evidence in another document
    images: tuple[tuple[int, str], ...] = ()  # (pdf page, what it shows) of image evidence in the own document


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    source: str
    split: str
    doc: str | None
    question: str
    facets: tuple[Facet, ...]
    pages: tuple[int, ...] = ()
    unanswerable: bool = False
    notes: str = ""
    reference: str = ""

    @property
    def required(self) -> tuple[Facet, ...]:
        return tuple(f for f in self.facets if f.required)

    @property
    def visual(self) -> bool:
        return any(f.images for f in self.facets)

    @property
    def other_docs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(d for f in self.facets for d, _ in f.elsewhere))

    @property
    def docs(self) -> tuple[str, ...]:
        """The question's own document first, then the other documents its evidence cites."""
        return tuple(dict.fromkeys((self.doc,) + self.other_docs if self.doc else self.other_docs))


class HeldOutRefused(ValueError):
    pass


FrozenRefused = HeldOutRefused


def is_heldout_path(path: Path) -> bool:
    """Inside a held-out directory: one whose name starts with a prefix of settings.HELDOUT."""
    return settings.heldout_marker(path) is not None


is_confirm_path = is_heldout_path


def h_dev_files() -> list[Path]:
    """Every delivered development file (dev.jsonl, dev2.jsonl, dev3.jsonl, ...)."""
    return sorted(H_DIR.glob("dev*.jsonl"))


def _printed(label: str | None) -> str | None:
    if not label:
        return None
    m = re.fullmatch(r"\D*(\d+)\D*", label)
    return m.group(1) if m else label.strip()


def _h_question(row: dict[str, Any]) -> Question:
    own = row["doc"]
    facets = []
    for f in row.get("facets") or []:
        ev = [e | {"pdf_page": e.get("pdf_page") or e.get("page")} for e in f.get("evidence") or []]
        facets.append(Facet(
            id=f["id"], text=f["text"], required=bool(f.get("required", True)),
            pages=tuple(sorted({e["pdf_page"] for e in ev if e.get("pdf_page") and (e.get("doc") or own) == own})),
            printed=tuple(dict.fromkeys(p for e in ev if (p := _printed(e.get("printed_page"))))),
            elsewhere=tuple(sorted({(e["doc"], e["pdf_page"]) for e in ev
                                    if e.get("pdf_page") and (e.get("doc") or own) != own})),
            images=tuple(dict.fromkeys((e["pdf_page"], str(e.get("what") or "")) for e in ev
                                       if e.get("kind") == "image" and e.get("pdf_page") and (e.get("doc") or own) == own)),
        ))
    pages = tuple(sorted({p for f in facets if f.required for p in f.pages}))
    return Question(row["id"], "H", row["split"], row["doc"], row["question"], tuple(facets), pages,
                    bool(row.get("unanswerable")), row.get("notes", ""), row.get("reference_answer", ""))


def load_h(paths: Path | Sequence[Path], allow_frozen: bool = False) -> dict[str, Question]:
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    out: dict[str, Question] = {}
    for path in map(Path, paths):
        frozen = is_heldout_path(path)
        if frozen and not allow_frozen:
            raise HeldOutRefused(f"{path} is held out; allow it only for final acceptance")
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            q = _h_question(json.loads(line))
            if q.split != ("frozen" if frozen else "dev"):
                raise ValueError(f"{path}: {q.id} has split {q.split!r}")
            if q.id in out:
                raise ValueError(f"duplicate question id {q.id}")
            out[q.id] = q
    return out


def load_all(h_paths: Iterable[Path] | None = None, allow_frozen: bool = False,
             sixten_set: bool = True) -> dict[str, Question]:
    """6x10 (where its research loader exists) plus task H; by default H means the development files only."""
    out = load_sixten() if sixten_set and load_sixten else {}
    h = load_h(list(h_paths) if h_paths is not None else h_dev_files(), allow_frozen)
    clash = set(out) & set(h)
    if clash:
        raise ValueError(f"ids in both sets: {sorted(clash)}")
    out.update(h)
    return out


try:  # research material, not released; imported last because it builds on Facet and Question
    from .sixten_set import load_sixten
except ImportError:
    load_sixten = None
