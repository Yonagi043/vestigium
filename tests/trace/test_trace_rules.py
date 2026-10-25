"""Rule and type text: what parses, what it means, and that every wrong rule is refused with where it is wrong
and a corrected example that itself parses."""

from __future__ import annotations

import pytest

from tglw.trace import rules

TYPES = {"hkex": ("hkex__*",), "nist": ("nist__*",)}

GOOD = [
    # R1 read wider; R2 follow the boundary; R3 after a cut; R4 counts; R6 images; scopes; tails
    "on read where from-hit in all do widen 1",
    "on read where from-hit and pages<=2 in type nist do widen 1",
    "on continues where same-section in type hkex do add next-page max 1 budget 2000t",
    "on continues in all do add first-line",
    "on cut in all do add unread-headings",
    "on cut where tokens>=6000 in doc hkex__sehk26073002144 do add rest max 2",
    "on zero where has-id in type nist do add absence budget 300t",
    "on view where scan in all do zoom x2",
    "on view where figure in all do zoom x2.5",
    "on view where table in all do zoom x2",
    "on read where figure in all do add image",
    "on read where from-hit and scan in type nist do widen 1, add image budget 3000t",
    "on zero in all do add absence",
    "on saturated in all do add coverage max 1",
    "on saturated where searches>=5 in all do add coverage",
    'on continues where same-section in all do add next-page, add first-line why "lists run over pages"',
    "type hkex = hkex__*",
    "type filings = enterprise_ir__10-K*, enterprise_ir__10-Q*",
]

BAD = [
    "",
    "when continues in all do add next-page",
    "on continue in all do add next-page",
    "on continues do add next-page",
    "on continues in do add next-page",
    "on continues in everything do add next-page",
    "on continues in doc do add next-page",
    "on continues in type Hkex do add next-page",
    "on continues in type sec do add next-page",
    "on continues in all add next-page",
    "on continues in all do",
    "on continues in all do add",
    "on continues in all do add next-pages",
    "on continues in all do remind the model to read on",
    "on continues in all do zoom x2",
    "on read in all do widen",
    "on read in all do widen 5",
    "on read in all do widen two",
    "on view in all do zoom 2",
    "on view in all do zoom x9",
    "on continues where samesection in all do add next-page",
    "on continues where pages in all do add next-page",
    "on continues where pages<=two in all do add next-page",
    "on read where same-section in all do widen 1",
    "on find where from-hit in all do add count",
    "on continues where has-id in all do add next-page",
    "on continues in all do add next-page max 0",
    "on continues in all do add next-page max 99",
    "on continues in all do add next-page budget 2000",
    "on continues in all do add next-page budget 99999t",
    "on continues in all do add next-page why lists",
    "on continues in all do add next-page max 1 max 2",
    "on continues in all do add next-page please",
    "type = hkex__*",
    "type hkex hkex__*",
    "type hkex =",
    "type hkex = hkex/*",
    "on zero in all do add next-page",
    "on saturated in all do widen 1",
    "on continues in all do add next-page, zoom x2",
    "on",
    "on continues where",
    "on read in all do add image",
    "on read where from-hit in all do widen 1, add image",
    "on find in all do add absence",
    "on find where scan in all do add count",
    "on cut where figure in all do add rest",
    "on find where has-id in all do add count",
    "on saturated in all do add absence",
    "on zero in all do add count",
    "on read in all do add coverage",
    "on saturated in all do add items",
    "on read where list in all do add items",
    "on saturated where searches in all do add coverage",
]


@pytest.mark.parametrize("text", GOOD)
def test_good_rules_parse_and_round_trip(text):
    r = rules.parse(text, TYPES)
    assert rules.parse(r.text(), TYPES).text() == r.text()


def test_at_least_thirty_wrong_rules():
    assert len(BAD) >= 30


@pytest.mark.parametrize("text", BAD)
def test_wrong_rules_are_refused_with_place_and_fix(text):
    with pytest.raises(rules.RuleError) as err:
        rules.parse(text, TYPES)
    e = err.value
    assert e.col >= 1 and e.msg and e.fix
    assert f"col {e.col}:" in str(e) and "e.g." in str(e)
    assert rules.check(e.fix, TYPES | {"sec": ("sec__*",)}) is None, e.fix  # the example itself is right


def test_unknown_type_only_with_types_given():
    assert rules.check("on continues in type sec do add next-page") is None
    assert "no type 'sec'" in rules.check("on continues in type sec do add next-page", TYPES)


def test_refs_and_type_name():
    assert rules.refs("on continues in type hkex do add next-page") == {"hkex"}
    assert rules.refs("on continues in all do add next-page") == set()
    assert rules.refs("type hkex = hkex__*") == set()
    assert rules.type_name("type hkex = hkex__*, sehk*") == "hkex"
    with pytest.raises(rules.RuleError):
        rules.type_name("on zero in all do add absence")


def test_scope_applies_to():
    r = rules.parse("on continues in type hkex do add next-page")
    assert r.applies_to("hkex__sehk26073002144", TYPES) and not r.applies_to("nist__x", TYPES)
    assert rules.parse("on zero in all do add absence").applies_to("anything", {})
    d = rules.parse("on zero in doc nist__x do add absence")
    assert d.applies_to("nist__x", {}) and not d.applies_to("nist__y", {})


def test_no_advice_actions_exist():
    for signal, actions in rules.ACTIONS.items():
        assert all(a.split()[0] in ("add", "widen", "zoom") for a in actions), signal


def test_help_lists_exactly_the_rule_words():
    """The help text and the parser agree on signals, actions and conditions."""
    from tglw.trace import lang
    rules_part = lang.HELP.split("Rules (", 1)[1]
    for signal, actions in rules.ACTIONS.items():
        line = next(x for x in rules_part.splitlines() if x.strip().startswith(signal + " "))
        for a in actions:
            assert a.split()[-1] in line, (signal, a)
    for c in rules.CONDS:
        assert c in rules_part, c
    for example in ("on continues where same-section in type hkex do add next-page max 1 budget 2000t",):
        assert example in rules_part and rules.check(example, {"hkex": ("hkex__*",)}) is None


def test_help_shows_the_tool_additions_as_rendered():
    """What help says the tool's own additions look like is what render writes."""
    from tglw.trace import lang, render
    for d in ({"k": "adjacent", "by": "tool", "src": "M8", "pages": [149], "tokens": 5},
              {"k": "grow", "by": "tool", "src": "M6", "pages": [151], "tokens": 5},
              {"k": "exact_note", "by": "tool", "src": "exact", "q": "SI-4", "n": 78, "tokens": 5},
              {"k": "saturation_note", "by": "tool", "src": "saturation_hint", "tokens": 5},
              {"k": "auto_zoom", "by": "tool", "src": "ADAPTIVE_ZOOM", "scale": 2.4}):
        shown = render._decision(d).rsplit(" ", 1)[0] if d.get("tokens") else render._decision(d)
        assert shown in lang.HELP, shown
    assert "rule r-7f3a" in lang.HELP and render._decision(
        {"k": "rule", "by": "rule", "src": "r-7f3a", "act": "add next-page", "tokens": 5}).startswith("rule r-7f3a")


def test_tool_texts_fit_gate_one():
    """Gate 1: the two new tools' descriptions together about 300 tokens (with names and parameters: trace tool 171,
    rules tool 66 + its description, as INT measured them), the language help at most 1,500."""
    from tglw.common.text import count_tokens
    from tglw.trace import lang
    assert count_tokens(lang.RULES_TOOL_DESCRIPTION) <= 63
    assert count_tokens(lang.HELP) <= 1500 and lang.HELP.endswith(lang.RULES_HELP)
    for action in ("list", "add", "change", "delete", "on", "off", "undo", "reset", "all-off", "history", "preview", "help"):
        assert action in lang.RULES_TOOL_DESCRIPTION, action
    assert rules.check(lang.RULES_TOOL_DESCRIPTION.split("'")[1]) is None  # its example is a rule that parses


def _fix(text: str, types=None) -> str:
    with pytest.raises(rules.RuleError) as err:
        rules.parse(text, types)
    return err.value.fix


def test_fix_examples_add_nothing_the_rule_did_not_ask_for():
    """Gate 3: haiku copied 'max 1 budget 2000t' from the examples into rules that had no such limits."""
    assert _fix("on continues in all do add nextpage") == "on continues in all do add next-page"
    assert _fix("on continues in all do add nextpage max 1 budget 2000t") == "on continues in all do add next-page max 1 budget 2000t"
    assert _fix("on cut in type nist do add next-page budget 3000t", TYPES) == "on cut in type nist do add rest budget 3000t"


def test_fix_examples_keep_the_documents_the_rule_named():
    """A fix must not widen the scope (that is wrong in gate 3 too)."""
    assert _fix("on table in doc enterprise_ir__X do add image") == "on read where table in doc enterprise_ir__X do add image"
    assert _fix("on truncated in type nist do add rest budget 3000t") == "on cut in type nist do add rest budget 3000t"


def test_errors_say_what_each_signal_means_and_which_one_the_rule_meant():
    msg = rules.check("on search in all do add absence")
    assert "zero (a search found nothing)" in msg and "cut (a read stopped at its budget" in msg
    assert "did you mean 'zero'?" in msg and msg.endswith("e.g. on zero in all do add absence")
    msg = rules.check("on continues in all do add rest")
    assert "'add rest' goes with cut (a read stopped at its budget; next: lists the rest)" in msg
    assert rules.check("on truncated in all do add rest").endswith("e.g. on cut in all do add rest")
    assert "write it with a hyphen: add first-line" in rules.check("on continues in all do add first line")


def test_quick_reference_lists_every_signal_with_its_actions():
    from tglw.common.text import count_tokens
    from tglw.trace import lang
    for signal, acts in rules.ACTIONS.items():
        line = next(x for x in lang.RULES_QUICK.splitlines() if x.strip().startswith(signal + " "))
        assert rules.GLOSS[signal] in line and all(a.split()[-1] in line for a in acts), signal
    assert count_tokens(lang.RULES_QUICK) <= 200
    assert all(f"{s} (" in lang.RULES_TOOL_DESCRIPTION for s in ("continues", "cut", "zero"))
