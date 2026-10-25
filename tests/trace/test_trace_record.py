"""The call-event collector: fields, validation, recording off, errors, sink failures, and that concurrent
calls in worker threads never share or misfile what they report."""

from __future__ import annotations

import contextvars
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tglw.trace import record


@pytest.fixture()
def sink(monkeypatch):
    got: list[dict] = []
    monkeypatch.delenv(record.TRACE_ENV, raising=False)
    monkeypatch.delenv(record.QUERY_ENV, raising=False)
    record.set_sink(got.append)
    record._take_lost()
    yield got
    record.set_sink(None)


def _deep_view_code(page: int) -> str:
    """Stands in for views code far below the server wrapper."""
    record.sig("continues", page=page + 1, sec="s17")
    record.dec("adjacent", src="M8", pages=[page - 1], tokens=12)
    record.addr(f"p{page}", kind="page", pages=[page])
    record.out(pages=[page - 1, page], units=["s17"], refs=[f"t{page}"])
    return f"== p{page} ==\ntext\ncontinues: p{page + 1} (s17)"


def test_event_has_the_format(sink):
    with record.call("read", {"doc": "d", "addrs": ["p150"]}, conv="stdio", client={"name": "x"}) as ev:
        ev.doc("d")
        ev.result(_deep_view_code(150))
    assert len(sink) == 1
    e = sink[0]
    assert record.validate(e) == []
    assert e["tool"] == "read" and e["doc"] == "d" and e["seq"] >= 1 and e["t1"] >= e["t0"]
    assert e["sig"] == [{"k": "continues", "page": 151, "sec": "s17"}]
    assert e["dec"][0]["k"] == "adjacent" and e["dec"][0]["by"] == "tool" and e["dec"][0]["tokens"] == 12
    assert e["addr"] == [{"in": "p150", "kind": "page", "pages": [150]}]
    assert e["out"]["pages"] == [149, 150] and e["out"]["parts"] == 1 and e["out"]["images"] == 0
    assert e["out"]["tokens"] > 0 and e["out"]["chars"] > 0
    assert "lost" not in e


def test_off_collects_nothing(sink, monkeypatch):
    monkeypatch.setenv(record.TRACE_ENV, "0")
    with record.call("read", {"doc": "d", "addrs": ["p1"]}) as ev:
        ev.result(_deep_view_code(1))
    assert sink == []
    assert record._take_lost() == {}  # nothing counted either


def test_error_is_recorded_and_raised(sink):
    with pytest.raises(ValueError):
        with record.call("search", {"doc": "d", "query": "q"}):
            raise ValueError("bad unit")
    assert sink[0]["err"] == {"type": "ValueError", "msg": "bad unit"}
    assert record.validate(sink[0]) == []


def test_a_failing_sink_never_reaches_the_tool(monkeypatch):
    def broken(event):
        raise OSError("disk full")
    record.set_sink(broken)
    try:
        with record.call("outline", {"doc": "d"}) as ev:
            ev.result("ok")
    finally:
        record.set_sink(None)


def test_query_can_be_hashed(sink, monkeypatch):
    monkeypatch.setenv(record.QUERY_ENV, "0")
    with record.call("search", {"doc": "d", "query": "food safety incident"}) as ev:
        ev.result("x")
    q = sink[0]["args"]["query"]
    assert q["len"] == len("food safety incident") and len(q["sha1"]) == 12
    assert record.said("SI-4") == {"len": 4, "sha1": record.said("SI-4")["sha1"]}  # keys taken from a query too


def test_query_kept_as_written_by_default(sink):
    args = {"doc": "d", "query": "SI-4", "k": 5}
    with record.call("search", args) as ev:
        ev.result("x")
    assert sink[0]["args"] == args and record.said("SI-4") == "SI-4"
    assert ":" in sink[0]["thread"]  # name and native id: worker threads share a name


def test_result_counts_parts_and_images(sink):
    class Image:  # anything that is not text counts as an image
        pass
    with record.call("region", {"doc": "d", "target": "fig1"}) as ev:
        ev.result(["caption", "second part", Image()])
    o = sink[0]["out"]
    assert o["parts"] == 2 and o["images"] == 1 and o["chars"] == len("caption") + len("second part")


def test_refs_are_capped(sink):
    with record.call("read", {"doc": "d", "addrs": ["s1"]}) as ev:
        record.out(refs=[f"t{i}" for i in range(record.REFS_MAX + 50)])
        ev.result("x")
    o = sink[0]["out"]
    assert len(o["refs"]) == record.REFS_MAX and o["refs_n"] == record.REFS_MAX + 50


def test_concurrent_calls_in_worker_threads_do_not_mix(sink):
    """The server runs tool calls in worker threads; each call's reports must stay with that call."""
    def tool(i: int) -> int:
        with record.call("read", {"doc": "d", "addrs": [f"p{i}"]}, conv=f"c{i % 3}") as ev:
            for _ in range(5):
                time.sleep(random.random() / 200)
                record.sig("continues", page=i, sec=f"s{i}")
            ev.result("x")
        return i
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sorted(pool.map(tool, range(40))) == list(range(40))
    assert len(sink) == 40
    for e in sink:
        i = int(e["args"]["addrs"][0][1:])
        assert len(e["sig"]) == 5 and all(s["page"] == i and s["sec"] == f"s{i}" for s in e["sig"])
        assert e["conv"] == f"c{i % 3}"
    assert len({e["seq"] for e in sink}) == 40
    assert record._take_lost() == {}


def test_a_thread_started_inside_a_call_loses_reports_visibly(sink):
    """A plain thread does not see the call: its report is dropped (never put on another call) and counted;
    started through copy_context().run it reaches the call."""
    with record.call("read", {"doc": "d", "addrs": ["p1"]}) as ev:
        t = threading.Thread(target=record.sig, args=("scan",), kwargs={"pages": [1]})
        t.start()
        t.join()
        ctx = contextvars.copy_context()
        t2 = threading.Thread(target=ctx.run, args=(record.sig, "qc"), kwargs={"pages": [1], "why": ["ocr"]})
        t2.start()
        t2.join()
        ev.result("x")
    with record.call("read", {"doc": "d", "addrs": ["p2"]}) as ev:
        ev.result("y")
    first, second = sink
    assert [s["k"] for s in first["sig"]] == ["qc"]  # the copied context reached the call
    assert second["lost"] == {"sig:scan": 1}  # the plain thread's report was counted, not misfiled
    assert "lost" not in first


def test_end_event_carries_what_was_lost(sink, monkeypatch):
    monkeypatch.setattr(record, "_started", True)
    record.sig("next", rest=["s2"])  # no call current
    record.end()
    assert sink[-1]["ev"] == "end" and sink[-1]["lost"] == {"sig:next": 1}
    assert record.validate(sink[-1]) == []


def test_start_writes_one_process_event(sink, monkeypatch):
    monkeypatch.setattr(record, "_started", False)
    monkeypatch.setenv(record.RUN_ENV, "run-7")
    record.start(pkg="0.2.0", switches={"TGLW_X": "1"})
    record.start(pkg="again")
    procs = [e for e in sink if e["ev"] == "proc"]
    assert len(procs) == 1 and procs[0]["run"] == "run-7" and procs[0]["pkg"] == "0.2.0"
    assert record.validate(procs[0]) == []


@pytest.mark.parametrize("bad, problem", [
    ({"v": 0, "ev": "call"}, "proc: missing"),
    ({"v": 1, "ev": "proc", "proc": "p", "t": 1.0}, "v: expected"),
    ({"v": 0, "ev": "nope"}, "unknown event type"),
])
def test_validate_reports_problems(bad, problem):
    assert any(problem in p for p in record.validate(bad))


def test_validate_rejects_unknown_kinds_and_untagged_decisions(sink):
    with record.call("read", {"doc": "d", "addrs": ["p1"]}) as ev:
        ev.sig("contines", page=2)
        ev.dec("adjacent", by="model", src="M8", pages=[2])
        ev.result("x")
    problems = record.validate(sink[0])
    assert any("unknown kind 'contines'" in p for p in problems)
    assert any("by must be" in p for p in problems)
    assert any("missing tokens" in p for p in problems)


def test_out_merges_over_a_call(sink, monkeypatch):
    monkeypatch.setattr(record, "REFS_MAX", 3)
    with record.call("read", {"doc": "d", "addrs": ["p5"]}) as ev:
        record.out(pages=[5], units=["s2"], refs=["t1", "t2"], chars_note="a")
        record.out(pages=[4, 5], units=["s2", "s3"], refs=["t2", "t3", "t4"], chars_note="b")
        ev.result("x")
    o = sink[0]["out"]
    assert o["pages"] == [4, 5] and o["units"] == ["s2", "s3"]
    assert o["refs"] == ["t1", "t2", "t3"] and o["refs_n"] == 4 and o["chars_note"] == "b"


def test_rewind_drops_what_was_thrown_away(sink):
    """Work computed and then discarded (a shrunk re-read, a page that did not fit) leaves no reports."""
    with record.call("read", {"doc": "d", "addrs": ["p5"]}) as ev:
        record.sig("continues", page=6, sec="s1")
        record.out(pages=[5], refs=["t1"])
        m = record.mark()
        record.sig("cut", rest=["s1 from t9"])
        record.dec("adjacent", src="M8", pages=[6], tokens=9)
        record.addr("p6", kind="page", pages=[6])
        record.out(pages=[6], refs=["t9"])
        record.rewind(m, kinds=("sig", "dec", "addr", "out"))
        m2 = record.mark()
        record.sig("scan", pages=[5])
        record.addr("p7", kind="page", pages=[7])
        record.rewind(m2, kinds=("addr",))  # keep the signal, drop the address the model did not ask for
        ev.result("x")
    e = sink[0]
    assert [s["k"] for s in e["sig"]] == ["continues", "scan"]
    assert e["dec"] == [] and e["addr"] == []
    assert e["out"]["pages"] == [5] and e["out"]["refs"] == ["t1"]
    assert record.validate(e) == []


def test_mark_and_rewind_do_nothing_outside_a_call(sink):
    assert record.mark() is None
    record.rewind(None)
    record.rewind(record.mark(), kinds=("sig",))


def test_quiet_drops_without_counting(sink):
    with record.quiet():
        record.sig("hits", mode="bm25", n=5, items=[])  # e.g. warming caches before any call
        record.doc("d")
    with record.call("outline", {"doc": "d"}) as ev:
        ev.result("x")
    assert "lost" not in sink[0]


def test_doc_from_deep_code(sink):
    with record.call("read", {"doc": "short name", "addrs": ["p1"]}) as ev:
        record.doc("registered__name")
        ev.result("x")
    assert sink[0]["doc"] == "registered__name"


def test_replay_field_is_kept(sink):
    with record.call("read", {"doc": "d", "addrs": ["p1"]}, replay={"src": "session.jsonl", "turn": 3, "i": 1}) as ev:
        ev.result("x")
    assert sink[0]["replay"] == {"src": "session.jsonl", "turn": 3, "i": 1}
    assert record.validate(sink[0]) == []


def test_recording_errors_never_reach_the_tool(sink):
    """Every public function swallows its own errors and counts them as lost; the tool's own result is kept."""
    with record.call("read", {"doc": "d", "addrs": ["p1"]}) as ev:
        record.out(pages=None)  # set(None) raises inside out()
        record.sig("continues", page=2)
        record.rewind(record.mark(), kinds=None)  # `in None` raises inside rewind()
        ev.result("text")
    with record.call("search", None):  # the event cannot be made: the call runs unrecorded
        pass
    record.lost("helper:excerpt")  # a helper computed only for the trace failed (views.tracing)
    with record.call("outline", {"doc": "d"}) as ev:
        ev.result("x")
    assert [e["tool"] for e in sink] == ["read", "outline"]
    assert sink[0]["sig"] == [{"k": "continues", "page": 2}] and sink[0]["out"]["tokens"] >= 1
    assert sink[1]["lost"] == {"error:out": 1, "error:rewind": 1, "error:call": 1, "helper:excerpt": 1}


def test_tool_errors_still_raise(sink):
    with pytest.raises(ValueError):
        with record.call("read", {"doc": "d", "addrs": ["p1"]}):
            raise ValueError("bad address")
    assert sink[0]["err"] == {"type": "ValueError", "msg": "bad address"}


def test_collect_when_recording_is_off(sink, monkeypatch):
    """Rules in effect need a call's signals with TGLW_TRACE=0: collected, handed to no sink, nothing lost."""
    monkeypatch.setenv(record.TRACE_ENV, "0")
    with record.call("read", {"doc": "d", "addrs": ["p1"]}, collect=True) as ev:
        record.sig("continues", page=2)
        seen = list(ev.data["sig"])
    with record.call("read", {"doc": "d", "addrs": ["p1"]}) as ev2:
        record.sig("continues", page=2)
    assert seen == [{"k": "continues", "page": 2}] and ev2 is record.NULL and sink == []


def test_via_marks_what_a_rule_added(sink):
    with record.call("read", {"doc": "d", "addrs": ["p150"]}):
        record.sig("continues", page=151)
        with record.via("r-7f3a"):
            record.sig("continues", page=152)
            record.dec("rule", by="rule", src="r-7f3a", act="add next-page", tokens=812)
    sig, dec = sink[0]["sig"], sink[0]["dec"]
    assert sig == [{"k": "continues", "page": 151}, {"k": "continues", "page": 152, "via": "r-7f3a"}]
    assert dec[0]["via"] == "r-7f3a" and record.validate(sink[0]) == []


def test_capture_keeps_replays_out_of_the_traces(sink, monkeypatch):
    """A preview's calls go to its own list, recording on or off; calls elsewhere still reach the sink."""
    with record.capture() as got:
        with record.call("read", {"doc": "d", "addrs": ["p1"]}) as ev:
            ev.result("x")
        with ThreadPoolExecutor(1) as pool:  # another context: not captured
            pool.submit(_one_call, "outline").result()
    monkeypatch.setenv(record.TRACE_ENV, "0")
    with record.capture() as off:
        with record.call("read", {"doc": "d", "addrs": ["p2"]}):
            record.sig("continues", page=3)
    assert [e["tool"] for e in got] == ["read"] and [e["tool"] for e in sink] == ["outline"]
    assert off[0]["sig"] == [{"k": "continues", "page": 3}]


def _one_call(tool: str) -> None:
    with record.call(tool, {"doc": "d"}) as ev:
        ev.result("y")
