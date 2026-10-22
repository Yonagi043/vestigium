"""Add the user's own PDFs to the library: Docling conversion, registry, sidecars, indexes, quality report.

Command line: `<COMMAND> ingest` or `python -m tglw.ingest`; see run.py. tglw.ingest.convert also runs alone in
an environment that only has Docling.
"""

from __future__ import annotations

from typing import Any


def add(*args: Any, **kwargs: Any) -> Any:
    from .run import add as _add

    return _add(*args, **kwargs)


def remove(*args: Any, **kwargs: Any) -> Any:
    from .run import remove as _remove

    return _remove(*args, **kwargs)
