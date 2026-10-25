"""The trace tool's text from events on disk: newest readings first, per conversation, filtered by document,
only ignored signals on request. Synthetic events written through the store into a temporary directory."""

from __future__ import annotations

import pytest

from tglw.common.paths import TRACE_ENV
from tglw.store import traces as store
from tglw.trace import query


def _call(proc, seq, conv, t0, tool, args, doc, out=None, sig=()):
    return {"v": 0, "ev": "call", "proc": proc, "seq": seq, "conv": conv, "client": None, "t0": t0, "t1": t0 + 0.1,
            "thread": "w", "tool": tool, "args": {"doc": doc, **args}, "doc": doc, "addr": [], "out": out or {},
            "sig": list(sig), "dec": [], "err": None}


@pytest.fixture()
def tracedir(tmp_path, monkeypatch):
    monkeypatch.setenv(TRACE_ENV, str(tmp_path))
    hits = {"k": "hits", "mode": "bm25", "n": 1, "items": [{"r": 1, "a": "t5", "p": 7, "sec": "s2"}]}
    events = [
        {"v": 0, "ev": "proc", "proc": "aaaa", "t": 0.0},
        _call("aaaa", 1, "c1", 1.0, "search", {"query": "first"}, "doc__a", {"tokens": 20}, [hits]),
        _call("aaaa", 2, "c1", 2.0, "read", {"addrs": ["p7"]}, "doc__a", {"pages": [7], "tokens": 50},
              [{"k": "continues", "page": 8, "sec": "s2"}]),
        _call("aaaa", 3, "c2", 3.0, "outline", {}, "doc__b", {"tokens": 30}, [{"k": "struct", "units": ["s1", "s2"]}]),
    ]
    writer = store.Writer(directory=tmp_path)
    for e in events:
        writer.append(e)
    writer.close()
    return tmp_path


def test_newest_reading_first(tracedir):
    text = query.show(last=2, directory=tracedir)
    first, second = text.split("\n\n")
    assert first.startswith("reading 1 · doc doc__b") and "map outline -> 2 listed" in first
    assert second.startswith("reading 2 · doc doc__a") and "read p7 <- hit 1#1 -> p7; continues p8" in second
    assert "ignored: continues p8 (2)" in second


def test_filter_by_document_and_ignored_only(tracedir):
    text = query.show(last=5, ignored=True, doc="doc__a", directory=tracedir)
    assert text.startswith("reading 1 · doc doc__a") and "find" not in text
    assert "ignored: continues p8 (2)" in text and "doc__b" not in text


def test_nothing_recorded(tmp_path):
    assert query.show(directory=tmp_path) == query.NONE
