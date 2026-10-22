"""Addresses that name their document: "nist__NIST.SP.800-53r5:AC-2(3)", "800-53r5:p45", "10-K-2025:t12".

The document is everything before the first colon (registered names have none): an exact name, or a
fragment of exactly one name, any case. Reader addresses that themselves hold a colon ("p11:0,300,800,600"
region boxes, "page: 36") are left whole.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable

from tglw.common import registry

SEP = ":"
_ADDR_BEFORE_COLON = re.compile(r"^(?:p\d+|page|s\d+|t\d+|tab\d+|fig\d+)$", re.I)
_SECTION_NUMBER = re.compile(r"^[\dIVXivx]+(?:\.[\dA-Za-z]+)+$")  # "5.1", "II.3.8.36"
_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")  # "2005.14165" names a document


class DocAddrError(ValueError):
    """A document prefix that names no document, or several; the message lists candidates."""


class UnknownDoc(DocAddrError):
    """A prefix that is no document name nor a fragment of one."""


def _names(names: Iterable[str] | None) -> list[str]:
    return list(names) if names is not None else [d.name for d in registry.docs()]


def match_doc(text: str, names: Iterable[str] | None = None) -> str:
    """The document an exact name or a unique fragment (any case) names; DocAddrError otherwise."""
    pool = _names(names)
    key = text.strip()
    if key in pool:
        return key
    low = key.lower()
    hits = [n for n in pool if low and low in n.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise DocAddrError(f"document {key!r} is ambiguous: {', '.join(hits[:8])}")
    close = difflib.get_close_matches(key, pool, n=5, cutoff=0.3)
    raise UnknownDoc(f"unknown document {key!r}" + (f"; candidates: {', '.join(close)}" if close else ""))


def split_doc_addr(addr: str, names: Iterable[str] | None = None, strict: bool = True) -> tuple[str | None, str]:
    """(document, address) for "doc:address"; (None, address) when no document is named. `names`
    limits the documents (an allowlist); default: every registered document with a cache. A prefix
    naming several documents raises DocAddrError; one naming none raises UnknownDoc when `strict`,
    else the whole text is an address ("Appendix B: Glossary" in the document at hand)."""
    s = addr.strip()
    head, sep, rest = s.partition(SEP)
    head = head.strip()
    if not sep or not head or not rest.strip() or _ADDR_BEFORE_COLON.match(head) \
            or (_SECTION_NUMBER.match(head) and not _ARXIV_ID.match(head)):
        return None, s
    try:
        return match_doc(head, names), rest.strip()
    except UnknownDoc:
        if strict:
            raise
        return None, s


def group_addrs(addrs: Iterable[str], default: str | None,
                names: Iterable[str] | None = None) -> list[tuple[str, list[str]]]:
    """Runs of consecutive addresses in one document, in the order given: [(doc, [address, ...]), ...].
    Addresses without a document (or with a prefix naming none) go to `default`; DocAddrError when
    there is none."""
    pool = _names(names)
    runs: list[tuple[str, list[str]]] = []
    for a in addrs:
        doc, rest = split_doc_addr(a, pool, strict=default is None)
        doc = doc or default
        if doc is None:
            raise DocAddrError(f"address {a!r} names no document: write it as 'document:{a.strip()}'")
        if runs and runs[-1][0] == doc:
            runs[-1][1].append(rest)
        else:
            runs.append((doc, [rest]))
    return runs
