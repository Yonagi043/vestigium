"""Traces from a session log and from raw events: blocks, labels for calls sent together, where addresses came
from, signals, the tool's own additions, ignored signals, and the text. Synthetic samples only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tglw.trace import events as ev_mod
from tglw.trace import record, render, session
from tglw.trace.build import base, page_set

DOC = "sample__report"

SEARCH_1 = ('search "safety incident" · blocks · top 5 · engine G\n'
            "1. t2111 · p150 (printed 141) · s17 BUSINESS | 2024 Food Safety Incident A franchised store…\n"
            "2. t2099 · p149 (printed 140) · s17 BUSINESS | …Franchisees are responsible for food safety…")
SEARCH_2 = ('search "penalty" · blocks · top 5 · engine G · exact text: 0 matches in this document\n'
            "1. t29 · p2 (printed i) · s1 WARNING | A joint stock company…\n"
            "most hits were listed before; whether the document states something at all: mode='literal' counts every match")
READ_1 = ("== p117 (printed 108) ==\n## s17 · BUSINESS\nThe market grew strongly over the last five years in every region.\n"
          "== p118 ==\nMore text that is long enough to count as a line of content here.\nnext: s17 from tab44")
READ_2 = ("== p150 (printed 141) ==\n## s17 · BUSINESS (cont.)\nThe franchised store compensated the customer in full.\n"
          "continues: p151 (s17)\n[adjacent pages that match your searches]\n== p149 ==\nStore monitoring covers key work areas.")
READ_3 = "[note] Note 12: bad address 'Note 12': use s12, p45, p45-47, t123, tab4, fig5"
GREP = 'search "SI-4" · literal · blocks · 3 matches in 2 blocks\n1. t1262 · p76 · 2× | …into SI-4.]\n2. t7920 · p366 · 1× | …into SI-4.]'
REGION = "fig3 · p15 · 688×897 px · about 713 image tokens · box p15:99,714,557,116 (pt, origin bottom-left)\ntext layer in the box:\nROOM 140"


def _session(tmp: Path) -> Path:
    """Turn 1: two searches sent together; then a cut read, a read with continues and an adjacent page, a bad
    address, a literal search, an image; then the answer."""
    lines = []

    def assistant(mid: str, uses: list[tuple[str, str, dict]]) -> None:
        lines.append({"type": "assistant", "message": {"id": mid, "content": [
            {"type": "tool_use", "id": uid, "name": f"mcp__vestigium__{tool}", "input": {"doc": DOC, **args}}
            for uid, tool, args in uses]}})

    def result(uid: str, text: str, image: bool = False) -> None:
        content = [{"type": "text", "text": text}] + ([{"type": "image", "source": {}}] if image else [])
        lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": content}]}})

    assistant("m1", [("u1", "search", {"query": "safety incident"}), ("u2", "search", {"query": "penalty"})])
    result("u1", SEARCH_1)
    result("u2", SEARCH_2)
    assistant("m2", [("u3", "read", {"addrs": ["s17"], "max_tokens": 8000})])
    result("u3", READ_1)
    assistant("m3", [("u4", "read", {"addrs": ["p150"]})])
    result("u4", READ_2)
    assistant("m4", [("u5", "read", {"addrs": ["Note 12"]})])
    result("u5", READ_3)
    assistant("m5", [("u6", "search", {"query": "SI-4", "mode": "literal"})])
    result("u6", GREP)
    assistant("m6", [("u7", "region", {"target": "fig3"})])
    result("u7", REGION, image=True)
    lines.append({"type": "assistant", "message": {"id": "m7", "content": [
        {"type": "text", "text": "The store paid compensation (p. 150) and was fined (pp. 149-150)."}]}})
    lines.append({"type": "assistant", "isSidechain": True, "message": {"id": "x", "content": [
        {"type": "tool_use", "id": "side", "name": "mcp__vestigium__read", "input": {"addrs": ["p9"]}}]}})
    path = tmp / "session.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def _events() -> list[dict]:
    """The same reading as raw events (two first calls overlap in time: sent together)."""
    def call(seq, t0, t1, tool, args, out=None, sig=(), dec=(), addr=()):
        return {"v": 0, "ev": "call", "proc": "p1", "seq": seq, "conv": "stdio", "client": None, "t0": t0, "t1": t1,
                "thread": "w", "tool": tool, "args": {"doc": DOC, **args}, "doc": DOC, "addr": list(addr),
                "out": out or {}, "sig": list(sig), "dec": list(dec), "err": None}
    hits1 = {"k": "hits", "mode": "bm25", "n": 2, "items": [{"r": 1, "a": "t2111", "p": 150, "sec": "s17"},
                                                             {"r": 2, "a": "t2099", "p": 149, "sec": "s17"}]}
    hits2 = {"k": "hits", "mode": "bm25", "n": 1, "items": [{"r": 1, "a": "t29", "p": 2, "sec": "s1"}]}
    return [
        {"v": 0, "ev": "proc", "proc": "p1", "t": 0.0},
        call(1, 1.00, 1.20, "search", {"query": "safety incident"}, {"tokens": 60}, [hits1]),
        call(2, 1.01, 1.25, "search", {"query": "penalty"}, {"tokens": 50},
             [hits2, {"k": "exact", "q": "penalty", "n": 0}, {"k": "saturated", "again": 1, "hits": 1}],
             [{"k": "exact_note", "by": "tool", "src": "exact", "n": 0, "tokens": 9},
              {"k": "saturation_note", "by": "tool", "src": "saturation_hint", "tokens": 25}]),
        call(3, 2.00, 2.30, "read", {"addrs": ["s17"], "max_tokens": 8000},
             {"pages": [117, 118], "units": ["s17"], "refs": ["t1", "t2"], "tokens": 40},
             [{"k": "next", "rest": ["s17 from tab44"], "why": "budget"}]),
        call(4, 3.00, 3.40, "read", {"addrs": ["p150"]},
             {"pages": [149, 150], "units": ["s17"], "refs": ["t2110", "t2111", "t2098"], "tokens": 45},
             [{"k": "continues", "page": 151, "sec": "s17"}],
             [{"k": "adjacent", "by": "tool", "src": "M8", "pages": [149], "tokens": 14}]),
        call(5, 4.00, 4.10, "read", {"addrs": ["Note 12"]}, {"tokens": 30}, (), (),
             [{"in": "Note 12", "kind": "label", "err": "bad address"}]),
        call(6, 5.00, 5.10, "search", {"query": "SI-4", "mode": "literal"}, {"tokens": 30},
             [{"k": "hits", "mode": "literal", "n": 2, "total": 3, "items": [{"r": 1, "a": "t1262", "p": 76},
                                                                             {"r": 2, "a": "t7920", "p": 366}]}]),
        call(7, 6.00, 6.50, "region", {"target": "fig3"},
             {"tokens": 40, "images": 1, "img": {"page": 15, "box": [99, 714, 557, 116], "scale": 1.5}}),
        {"v": 0, "ev": "end", "proc": "p1", "t": 7.0, "lost": {"sig:scan": 1}},
    ]


@pytest.fixture()
def session_trace(tmp_path):
    return session.trace_of(_session(tmp_path))


def test_session_blocks_turns_and_origins(session_trace):
    t = session_trace
    assert [s.label for s in t.steps] == ["1a", "1b", "2", "3", "4", "5", "6"]
    assert [s.call.block for s in t.steps] == ["find", "find", "read", "read", "read", "grep", "view"]
    assert [s.origin for s in t.steps] == [None, None, "hit", "hit", "new", None, "new"]
    assert (t.steps[2].origin_step, t.steps[2].origin_rank) == ("1a", 1)  # s17 is in hit 1 of the first search


def test_session_signals_and_tool_additions(session_trace):
    t = session_trace
    s2 = t.steps[1].call
    assert s2.count == 0 and {"k": "saturated"} in s2.signals
    assert [d["k"] for d in s2.decisions] == ["exact_note", "saturation_note"]
    read1, read2, bad = t.steps[2].call, t.steps[3].call, t.steps[4].call
    assert read1.pages == [117, 118] and read1.signals[0] == {"k": "cut", "rest": ["s17 from tab44"], "why": "budget"}
    assert read2.pages == [149, 150] and read2.decisions[0]["k"] == "adjacent" and read2.decisions[0]["pages"] == [149]
    assert bad.addr_errors == [{"in": "Note 12", "err": READ_3.split(": ", 1)[1]}]  # why, as the note gives it
    assert t.steps[5].call.count == 3 and t.steps[6].call.pages == [15] and t.steps[6].call.images == 1
    assert t.answer == {"chars": len("The store paid compensation (p. 150) and was fined (pp. 149-150)."), "cites": [149, 150]}


def test_ignored_signals(session_trace):
    got = {(x["k"], x["step"], x["target"]) for x in session_trace.ignored}
    assert got == {("cut", "2", "s17"), ("continues", "3", "p151")}


def test_rendered_trace(session_trace):
    text = render.render(session_trace)
    lines = text.splitlines()
    assert lines[0].startswith('1a  find "safety incident" -> hits 2 on p150 p149')
    assert "2   read s17 max=8000 <- hit 1a#1 -> p117-118; cut next s17 from tab44" in text
    assert "<- hit 1a#1 -> p149-150; continues p151; tool +p149 (adjacent)" in text
    assert "read Note 12 <- new -> nothing (bad address 'Note 12')" in text
    assert "grep \"SI-4\" mode=literal" not in text and 'grep "SI-4" -> count 3 on p76 p366' in text
    assert "view fig3 <- new -> image p15" in text and "+1 img" in text
    assert "stop after 6 · read p15 p117-118 p149-150" in text and "ignored: cut s17 (2); continues p151 (3)" in text
    assert text.endswith("cites p149-150")


def test_ignored_only_view(session_trace):
    text = render.render(session_trace, ignored_only=True)
    assert "find" not in text and text.splitlines()[-1].startswith("ignored:")


def test_events_give_the_same_trace(tmp_path, session_trace):
    """The server's raw events and the session log of the same reading translate to the same steps."""
    (trace,) = ev_mod.traces_of(_events())
    def key(t):
        return [(s.label, s.call.block, s.origin, s.origin_step, s.call.pages) for s in t.steps]
    assert key(trace) == key(session_trace)
    assert {(x["k"], x["step"]) for x in trace.ignored} == {(x["k"], x["step"]) for x in session_trace.ignored}
    assert trace.lost == {"sig:scan": 1} and "lost reports: sig:scan 1" in render.render(trace)


def test_replay_turns_override_times():
    evs = [e for e in _events() if e["ev"] == "call"]
    for i, e in enumerate(evs):
        e["t0"], e["t1"] = float(i), float(i) + 0.5  # times say: one call after another
        e["replay"] = {"src": "s", "turn": 1 if i < 2 else i, "i": i}
    (trace,) = ev_mod.traces_of(evs)
    assert [s.label for s in trace.steps][:2] == ["1a", "1b"]


def test_revisit_is_again():
    evs = [e for e in _events() if e["ev"] == "call"][:4]
    again = dict(evs[3], seq=9, t0=9.0, t1=9.1, sig=[], dec=[])
    (trace,) = ev_mod.traces_of(evs + [again])
    assert trace.steps[-1].origin == "again"


def test_conversations_are_separate_traces():
    evs = [e for e in _events() if e["ev"] == "call"]
    evs[0] = dict(evs[0], conv="http-a")
    traces = ev_mod.traces_of(evs)
    assert sorted(t.conv for t in traces) == ["http-a", "stdio"]


def test_pointer_lines_the_tool_took_out_are_not_signals():
    """M8's adjacent page and a rule's rest replace the pointer line they follow (LANG_SPEC 11.1); the pointer
    that came with the added text stays."""
    evs = [e for e in _events() if e["ev"] == "call"][:4]
    evs[2] = dict(evs[2], out={"pages": [117, 118, 119], "units": ["s17"], "tokens": 2040},
                  sig=[{"k": "next", "rest": ["s17 from tab44"], "why": "budget"},
                       {"k": "next", "rest": ["s17 from t70"], "why": "budget"}],
                  dec=[{"k": "rule", "by": "rule", "src": "r-7f3a", "act": "add rest", "sig": "cut",
                        "pages": [118, 119], "tokens": 2000, "dropped": {"k": "cut"}}])
    evs[3] = dict(evs[3], out={"pages": [149, 150, 151], "units": ["s17"], "tokens": 60},
                  sig=[{"k": "continues", "page": 151, "sec": "s17"}, {"k": "continues", "page": 152, "sec": "s17"}],
                  dec=[{"k": "adjacent", "by": "tool", "src": "M8", "pages": [151], "tokens": 14, "dropped_continues": 151}])
    (trace,) = ev_mod.traces_of(evs)
    assert [x["rest"] for x in trace.steps[2].call.signals if x["k"] == "cut"] == [["s17 from t70"]]
    assert [x["page"] for x in trace.steps[3].call.signals if x["k"] == "continues"] == [152]
    text = render.render(trace)
    assert "-> p117-119; cut next s17 from t70; rule r-7f3a add rest +p118-119 2000t" in text
    assert "-> p149-151; continues p152; tool +p151 (adjacent)" in text
    assert {(x["k"], x["target"]) for x in trace.ignored} == {("cut", "s17"), ("continues", "p152")}


def test_headings_a_rule_listed_are_a_map_for_later_reads():
    evs = [e for e in _events() if e["ev"] == "call"][:3]
    evs[2] = dict(evs[2], dec=[{"k": "rule", "by": "rule", "src": "rec-cut", "act": "add unread-headings",
                                "sig": "cut", "listed": ["s18", "s19"], "tokens": 30}])
    later = dict(evs[2], seq=9, t0=9.0, t1=9.1, args={"doc": DOC, "addrs": ["s19"]},
                 out={"pages": [130], "units": ["s19"], "tokens": 20}, sig=[], dec=[])
    (trace,) = ev_mod.traces_of(evs + [later])
    assert (trace.steps[-1].origin, trace.steps[-1].origin_step) == ("map", "2")
    assert "rule rec-cut add unread-headings s18 s19 30t" in render.render(trace)


@pytest.mark.parametrize("d, shown", [
    ({"act": "widen 1", "pages": [149, 151], "tokens": 1640}, "rule r-7f3a widen 1 +p149 +p151 1640t"),
    ({"act": "add first-line", "first": 151, "tokens": 38}, "rule r-7f3a add first-line p151 38t"),
    ({"act": "add count", "keys": [{"key": "SI-4", "n": 78}, {"key": {"len": 6, "sha1": "ab"}, "n": 0}], "tokens": 40},
     'rule r-7f3a add count "SI-4" 78, (key 6 chars) 0 40t'),
    ({"act": "zoom x2", "from": 1.5, "to": 2.0, "tokens": 600}, "rule r-7f3a zoom x2 600t"),
    ({"act": "add image", "target": "fig5", "tokens": 840}, "rule r-7f3a add image fig5 840t"),
    ({"act": "add next-page", "skipped": [{"what": "p152", "why": "budget"}]},
     "rule r-7f3a add next-page held back p152 (budget)"),
])
def test_rule_decisions_render(d, shown):
    assert render._decision({"k": "rule", "by": "rule", "src": "r-7f3a", **d}) == shown


@pytest.mark.parametrize("addr, b, pages", [("s29 from t979", "s29", set()), ("p12-14", "p12-14", {12, 13, 14}),
                                             ("p15:10,20,30,40", "p15", {15}), ("tab4", "tab4", set())])
def test_addresses(addr, b, pages):
    assert base(addr) == b and page_set(addr) == pages


def test_tool_counts_render_from_raw_events():
    """INT's dec says how many identifiers were counted (keys=1); which ones, and how many matches, are in the
    signal: the trace shows them, and never fails on the number."""
    evs = [e for e in _events() if e["ev"] == "call"][:1]
    evs[0] = dict(evs[0], sig=evs[0]["sig"] + [{"k": "exact", "n": 0, "pages": [], "pages_n": 0},
                                              {"k": "ident", "items": [{"key": "SI-4", "n": 78, "shown": ["t1262"], "pages": [76]}]}],
                  dec=[{"k": "exact_note", "by": "tool", "src": "EXACT_CHECK_WORDS", "tokens": 9},
                       {"k": "ident_hits", "by": "tool", "src": "M6.ident_hits", "keys": 1, "tokens": 40}])
    (trace,) = ev_mod.traces_of(evs)
    text = render.render(trace)
    assert 'tool +count 0 9t; tool +count "SI-4" 78 40t' in text
    assert {"r": 0, "a": ["t1262"], "p": [76], "sec": None, "ident": "SI-4"} in trace.steps[0].call.hits


def test_rule_tool_calls_are_no_steps():
    """A call of the rule tool is recorded (LANG_SPEC 11.8) but is no reading step: the trace says where the rules
    changed."""
    evs = [e for e in _events() if e["ev"] == "call"][:3]
    rule = dict(evs[2], seq=9, t0=2.5, t1=2.6, tool="rules", doc=None, out={"tokens": 12}, sig=[], dec=[],
                args={"action": "add", "text": "on continues in all do add next-page max 1", "why": "p151 missed"})
    (trace,) = ev_mod.traces_of(evs + [rule])
    assert [s.call.tool for s in trace.steps] == ["search", "search", "read"]
    assert trace.rules == [{"after": "2", "action": "add", "text": "on continues in all do add next-page max 1",
                            "error": False}]
    assert "rules changed: add on continues in all do add next-page max 1 after 2" in render.render(trace)
    assert record.validate(rule) == []


def test_preview_shows_what_rules_change():
    """A reading replayed with the rules off and on: the step a rule acts on, what it added, the later step that
    only read what it had added, and the sum."""
    evs = [e for e in _events() if e["ev"] == "call"][:4]
    later = dict(evs[3], seq=9, t0=9.0, t1=9.1, args={"doc": DOC, "addrs": ["p151"]},
                 out={"pages": [151], "units": ["s17"], "tokens": 700}, sig=[], dec=[])
    (off,) = ev_mod.traces_of(evs + [later])
    on_evs = [dict(e) for e in evs + [later]]
    on_evs[3] = dict(on_evs[3], out={**on_evs[3]["out"], "pages": [149, 150, 151]},
                     dec=on_evs[3]["dec"] + [{"k": "rule", "by": "rule", "src": "rec-continues", "act": "add next-page",
                                              "sig": "continues", "pages": [151], "tokens": 769}])
    (on,) = ev_mod.traces_of(on_evs)
    text = render.preview([(off, on), (off, off)])
    assert "      + rule rec-continues add next-page +p151 769t" in text
    assert "read p151 <- continues 3 -> p151 · 700t · already added at 3" in text
    assert "reading 2 · doc " in text and text.splitlines()[-2].endswith(": unchanged")
    assert text.endswith("total: rules act on 1 step in 1 of 2 readings · +769 tokens · 1 later step read what they added")
