"""The library: one index over every document's passages, searched without naming a document, and
addresses that name their document ("nist__NIST.SP.800-53r5:AC-2(3)")."""

from .addr import DocAddrError, UnknownDoc, group_addrs, match_doc, split_doc_addr
from .rank import Hit, Result, query, search
from .store import build, db_path, drop, stale, update
from .view import search_text

__all__ = ["DocAddrError", "Hit", "Result", "UnknownDoc", "build", "db_path", "drop", "group_addrs", "match_doc",
           "query", "search", "search_text", "split_doc_addr", "stale", "update"]
