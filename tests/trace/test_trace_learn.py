"""Rule suggestions from recorded readings: only when what a signal pointed to was wanted after all (read later, or
in another reading of the same document), repeated on one type of document; never what the rules already do or what
the user turned down; the same suggestion gets the same id every time. Synthetic readings only."""

from __future__ import annotations

from tglw.trace import events as ev_mod, learn, rules


def _call(conv: str, seq: int, t: float, tool: str, args: dict, pages=(), sig=(), doc="hkex__a") -> dict:
    return {"v": 0, "ev": "call", "proc": "p", "seq": seq, "conv": conv, "client": None, "t0": t, "t1": t + 0.1,
            "thread": "w", "tool": tool, "args": {"doc": doc, **args}, "doc": doc, "addr": [],
            "out": {"pages": list(pages), "tokens": 100}, "sig": list(sig), "dec": [], "err": None}


def _readings() -> list:
    evs = [
        # A, A2: read p150 of hkex__a, 'continues: p151' left unread
        _call("A", 1, 1.0, "read", {"addrs": ["p150"]}, [150], [{"k": "continues", "page": 151, "sec": "s17"}]),
        _call("A2", 2, 2.0, "read", {"addrs": ["p150"]}, [150], [{"k": "continues", "page": 151, "sec": "s17"}]),
        # B: another reading of hkex__a reads p151
        _call("B", 3, 3.0, "read", {"addrs": ["p151"]}, [151]),
        # C: hkex__b, p21 read two turns after its 'continues'
        _call("C", 4, 4.0, "read", {"addrs": ["p20"]}, [20], [{"k": "continues", "page": 21, "sec": "s3"}], doc="hkex__b"),
        _call("C", 5, 5.0, "search", {"query": "x"}, doc="hkex__b"),
        _call("C", 6, 6.0, "read", {"addrs": ["p21"]}, [21], doc="hkex__b"),
        # D: a nist reading whose 'continues' nobody ever read: no evidence
        _call("D", 7, 7.0, "read", {"addrs": ["p5"]}, [5], [{"k": "continues", "page": 6, "sec": "s1"}], doc="nist__x"),
    ]
    return sorted(ev_mod.traces_of(evs), key=lambda t: t.conv)


def test_a_signal_wanted_after_all_on_one_type_is_suggested():
    traces = _readings()
    (s,) = learn.suggest(traces)
    assert s.rule == "on continues in type hkex do add next-page max 1" and s.typedef == "type hkex = hkex__*"
    assert rules.check(s.typedef) is None and rules.check(s.rule, {"hkex": ("hkex__*",)}) is None
    assert s.why == ("in 3 of 3 readings of hkex documents, the page after 'continues' was read later or in another "
                     "reading (3 times)")
    assert [traces[i].conv for i in s.readings] == ["A", "A2", "C"]


def test_not_from_too_little():
    traces = [t for t in _readings() if t.conv in ("A", "B", "C")]  # two events only
    assert learn.suggest(traces) == []


def test_what_the_rules_do_or_the_user_turned_down_is_not_suggested_again():
    traces = _readings()
    assert learn.suggest(traces, rules=["on continues in all do add next-page"]) == []
    assert learn.suggest(traces, rules=["on continues where pages<=2 in all do add next-page max 1"]) != []  # narrower
    turned_down = {learn.canonical('on continues in type hkex do add next-page max 1 why "no"')}
    assert learn.suggest(traces, suppressed=turned_down) == []


def test_the_same_suggestion_has_the_same_id():
    a, b = learn.suggest(_readings()), learn.suggest(_readings())
    assert a == b and a[0].id.startswith("s-") and len(a[0].id) == 6


def test_a_document_without_a_type_is_named_itself():
    evs = [_call(c, i, float(i), "read", {"addrs": ["p1"]}, [1], [{"k": "continues", "page": 2, "sec": "s1"}], doc="report")
           for i, c in enumerate(("A", "B", "C"), 1)] + [_call("D", 9, 9.0, "read", {"addrs": ["p2"]}, [2], doc="report")]
    (s,) = learn.suggest(ev_mod.traces_of(evs))
    assert s.rule == "on continues in doc report do add next-page max 1" and s.typedef is None
