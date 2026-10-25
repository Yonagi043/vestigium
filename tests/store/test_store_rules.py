import json
import multiprocessing
import os
import stat

import pytest

from tglw.store import rules
from tglw.trace import rules as lang

R2 = "on continues where same-section in type hkex do add next-page max 1 budget 2000t"
R3 = "on cut in all do add unread-headings"
HKEX = "type hkex = hkex__*"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from tglw.common.paths import RULES_ENV

    rec = tmp_path / "recommended.json"
    rec.write_text(json.dumps({"release": "2.0", "entries": []}))
    monkeypatch.setattr(rules, "RECOMMENDED", rec)
    monkeypatch.setenv(RULES_ENV, str(tmp_path / "rules"))
    return tmp_path / "rules"


def shipped(path, *entries, release="2.0"):
    path.write_text(json.dumps({"release": release, "entries": list(entries)}))


def layer(d):
    data = json.loads((d / "rules.json").read_text())
    data.pop("version")
    return data


def test_add_list_and_private_files(home):
    t = rules.add(HKEX, "agent", "hkex filings")
    r = rules.add(R2, "agent", "follow page breaks")
    eff = rules.effective(home)
    assert [(e.id, e.kind, e.state) for e in eff] == [(t, "type", "user"), (r, "rule", "user")]
    assert isinstance(eff[1].parsed, lang.Rule) and rules.load(home).types == {"hkex": ("hkex__*",)}
    assert eff[1].source["why"] == "follow page breaks" and t.startswith("r-")
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert {stat.S_IMODE(p.stat().st_mode) for p in home.iterdir()} == {0o600}


def test_bad_text_and_unknown_type_are_refused(home):
    with pytest.raises(lang.RuleError):
        rules.add("on continues do add next-page", "agent", "no scope")
    with pytest.raises(lang.RuleError):
        rules.add(R2, "agent", "type hkex not defined yet")
    assert not (home / "rules.json").exists()


def test_a_used_type_cannot_go(home):
    t = rules.add(HKEX, "agent", "")
    r = rules.add(R2, "agent", "")
    for change in (lambda: rules.delete(t, "agent", ""), lambda: rules.set_on(t, False, "agent", ""),
                   lambda: rules.change(t, "type hk = hkex__*", "agent", "")):
        with pytest.raises(rules.StoreError, match=f"type hkex is still used by {r}; delete or change"):
            change()
    rules.delete(r, "agent", "")
    rules.delete(t, "agent", "")
    assert rules.effective(home) == []


def test_every_change_undoes(home):
    t = rules.add(HKEX, "agent", "")
    steps = [(lambda: rules.add(R2, "agent", ""), True),
             (lambda: rules.change(t, "type hkex = hkex__*, hk__*", "agent", ""), True),
             (lambda: rules.set_on(t, False, "agent", ""), True),
             (lambda: rules.set_on(t, True, "agent", ""), False),  # already on: the layer stays as it was
             (lambda: rules.delete(t, "agent", ""), True),
             (lambda: rules.all_off("agent", ""), True),
             (lambda: rules.reset("agent", ""), True)]
    for step, changes in steps:
        before = layer(home)
        step()
        assert (layer(home) != before) == changes
        assert rules.undo("agent").startswith("undid change")
        assert layer(home) == before  # all but the version number
    assert rules.undo("agent").startswith("undid change 1 (add")
    assert layer(home)["entries"] == []
    with pytest.raises(rules.StoreError, match="nothing to undo"):
        rules.undo("agent")


def test_all_off_and_reset(home, monkeypatch):
    shipped(rules.RECOMMENDED, {"id": "rec-cut", "kind": "rule", "ver": 1, "text": R3, "why": "R3"})
    mine = rules.add("on continues in all do add first-line", "agent", "")
    assert [e.id for e in rules.effective(home)] == ["rec-cut", mine]
    rules.all_off("agent", "back to v1")
    assert rules.effective(home) == []  # nothing applies: the v1 behaviour
    rules.reset("agent", "factory")
    assert [(e.id, e.state, e.source["by"]) for e in rules.effective(home)] == [("rec-cut", "as-shipped", "system")]
    shipped(rules.RECOMMENDED)
    rules.reset("agent", "factory")
    assert rules.effective(home) == []  # an empty recommended set: factory is v1


def test_upgrades_keep_what_the_user_did(home):
    rec = rules.RECOMMENDED
    shipped(rec, {"id": "rec-a", "kind": "rule", "ver": 1, "text": R3},
            {"id": "rec-b", "kind": "rule", "ver": 1, "text": "on continues in all do add first-line"},
            {"id": "rec-c", "kind": "rule", "ver": 1, "text": "on cut in all do add rest"})
    rules.set_on("rec-b", False, "agent", "user switched it off")
    copy = rules.change("rec-c", "on cut in all do add rest max 2", "agent", "fewer")
    shipped(rec, {"id": "rec-a", "kind": "rule", "ver": 2, "text": R3 + " max 1"},
            {"id": "rec-b", "kind": "rule", "ver": 2, "text": "on continues in all do add first-line max 1"},
            {"id": "rec-c", "kind": "rule", "ver": 2, "text": "on cut in all do add rest max 1"},
            {"id": "rec-d", "kind": "rule", "ver": 1, "text": "on zero in all do add absence"}, release="0.2")
    by_id = {e.id: e for e in rules.load(home).entries}
    assert (by_id["rec-a"].on, by_id["rec-a"].ver, by_id["rec-a"].state) == (True, 2, "as-shipped")  # untouched: new
    assert (by_id["rec-b"].on, by_id["rec-b"].state, by_id["rec-b"].newer_shipped) == (False, "off", True)
    assert (by_id["rec-c"].on, by_id["rec-c"].state, by_id["rec-c"].newer_shipped) == (False, "replaced", True)
    assert by_id[copy].on and by_id[copy].text.endswith("max 2") and "changed from rec-c" in by_id[copy].source["why"]
    assert by_id["rec-d"].on and by_id["rec-d"].source["why"] == "recommended in v0.2"  # a new one applies
    shipped(rec, {"id": "rec-a", "kind": "rule", "ver": 2, "text": R3})
    gone = {e.id: e for e in rules.load(home).entries}["rec-b"]
    assert (gone.on, gone.state) == (False, "not-shipped")


def _add_many(directory, n):
    for i in range(n):
        rules.add(f"on cut in doc d{os.getpid()}-{i} do add rest", "agent", "", directory=directory)


def test_processes_do_not_lose_changes(home):
    procs = [multiprocessing.get_context("fork").Process(target=_add_many, args=(home, 10)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert len(rules.effective(home)) == 40 and json.loads((home / "rules.json").read_text())["version"] == 40
    assert len(rules.history(100, home)) == 40


def test_history_is_trimmed(home, monkeypatch):
    monkeypatch.setattr(rules, "HISTORY_MAX_LINES", 10)
    monkeypatch.setattr(rules, "HISTORY_KEEP", 4)
    for i in range(12):
        rules.add(f"on cut in doc d{i} do add rest", "agent", f"#{i}")
    assert len((home / "history.jsonl").read_text().splitlines()) <= 10
    assert [h["why"] for h in rules.history(2, home)] == ["#11", "#10"] and "before" not in rules.history(1, home)[0]


def test_an_existing_directory_keeps_its_permissions(tmp_path, monkeypatch):
    rec = tmp_path / "recommended.json"
    shipped(rec)
    monkeypatch.setattr(rules, "RECOMMENDED", rec)
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    rules.add(R3, "agent", "", directory=shared)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE((shared / "rules.json").stat().st_mode) == 0o600


def test_the_shipped_recommended_layer_parses():
    shipped_layer = json.loads(rules.Path(rules.__file__).with_name("recommended.json").read_text())
    ids = [e["id"] for e in shipped_layer["entries"]]
    assert len(ids) == len(set(ids)) and all(i.startswith("rec-") for i in ids) and shipped_layer["release"]
    types = {}
    for e in shipped_layer["entries"]:
        parsed = lang.parse(e["text"], types)
        assert e["kind"] == ("type" if isinstance(parsed, lang.TypeDef) else "rule")
        if isinstance(parsed, lang.TypeDef):
            types[parsed.name] = parsed.patterns


def test_factory_is_the_shipped_layer(tmp_path):
    shipped_ids = [e["id"] for e in json.loads(rules.RECOMMENDED.read_text())["entries"]]
    d = tmp_path / "fresh"
    assert [e.id for e in rules.effective(d)] == shipped_ids  # a new library starts with the recommended rules
    rules.add("on cut in all do add unread-headings", "agent", "", directory=d)
    rules.set_on(shipped_ids[0], False, "agent", "", directory=d) if shipped_ids else None
    rules.reset("agent", "factory", directory=d)
    assert [e.id for e in rules.effective(d)] == shipped_ids
    rules.all_off("agent", "v1", directory=d)
    assert rules.effective(d) == []


def test_tests_run_without_rules_unless_asked():
    assert "TGLW_RULES" in os.environ  # tests/conftest.py sets 0 when the run did not set it


def test_by_default_the_recommended_rules_apply(tmp_path, monkeypatch):
    """With TGLW_RULES unset (the default a user gets), a fresh library has the shipped rules in force; the
    executor running them end to end is INT's half of this check."""
    monkeypatch.delenv("TGLW_RULES", raising=False)
    shipped_entries = json.loads(rules.RECOMMENDED.read_text())["entries"]
    in_force = rules.effective(tmp_path / "fresh")
    assert [(e.id, e.text) for e in in_force] == [(e["id"], e["text"]) for e in shipped_entries]
    assert all(isinstance(e.parsed, lang.Rule) and e.on for e in in_force if e.kind == "rule")


def test_an_evaluation_can_leave_the_recommended_layer_out(tmp_path, monkeypatch):
    d = tmp_path / "fresh"
    shipped_ids = [e["id"] for e in json.loads(rules.RECOMMENDED.read_text())["entries"]]
    assert [e.id for e in rules.effective(d)] == shipped_ids and shipped_ids
    rules.set_on(shipped_ids[0], False, "agent", "an override of a shipped rule", directory=d)
    monkeypatch.setenv(rules.RECOMMENDED_SWITCH, "0")
    assert rules.load(d).entries == () and rules.effective(d) == []  # list, effective, overrides: none of it shows
    with pytest.raises(rules.StoreError, match=f"no rule or type {shipped_ids[0]}"):
        rules.set_on(shipped_ids[0], True, "agent", "", directory=d)  # the layer is out of sight
    mine = rules.add("on zero in all do add absence", "agent", "the evaluation's own rule", directory=d)
    assert [e.id for e in rules.effective(d)] == [mine]
    monkeypatch.delenv(rules.RECOMMENDED_SWITCH)
    assert [e.id for e in rules.effective(d)] == [*shipped_ids[1:], mine]  # the override is back in force


def test_declined_and_accepted_suggestions(home):
    wanted, unwanted = "on cut in all do add rest max 2", "on zero in all do add absence"
    assert rules.key('on  cut in all do add rest max 2 why "x"') == wanted
    rules.decline(unwanted, "user", "not useful here")
    assert unwanted in rules.suppressed(home) and wanted not in rules.suppressed(home)
    rid = rules.add(wanted, "user", "follow the rest of a cut section", suggested=True)
    entry = next(e for e in rules.load(home).entries if e.id == rid)
    assert (entry.source["by"], entry.source["accepted_by"], entry.source["how"]) == ("system", "user", rules.SUGGESTED)
    rules.set_on(rid, False, "user", "")
    assert wanted in rules.suppressed(home)  # in the user layer, on or off: not suggested again
    assert rules.undo("user").startswith("undid change")
    assert rules.undo("user").startswith("undid change") and wanted not in rules.suppressed(home)
    assert rules.undo("user").startswith("undid change 1 (decline") and unwanted not in rules.suppressed(home)
    rules.decline(unwanted, "user", "")
    rules.reset("user", "factory")
    assert unwanted not in rules.suppressed(home)  # a reset forgets declined suggestions too


def test_a_suggested_type_is_marked_too(home):
    tid = rules.add("type hkex = hkex__*", "user", "s-1a2b, user agreed", suggested=True)
    rid = rules.add("on continues where same-section in type hkex do add next-page max 1", "user", "s-1a2b", suggested=True)
    by_id = {e.id: e for e in rules.load(home).entries}
    assert by_id[tid].kind == "type" and by_id[tid].source["how"] == rules.SUGGESTED
    assert by_id[rid].source["accepted_by"] == "user" and rules.key("type  hkex = hkex__*") in rules.suppressed(home)
