"""The rules tool (behind TGLW_RULES_TOOL): the agent lists, adds, changes, deletes, switches on and off, undoes
and previews the reading rules the tools carry out (tglw.store.rules keeps them; tglw.trace.rules reads them;
executor runs them; preview replays recorded readings with them). One tool; `action` says what to do, `text` holds
the rule (or a rule's id), `why` what the user asked for. A rule written wrong or a change the store refuses comes
back as the call's error, saying where and how to fix it."""

from __future__ import annotations

import re
import time

from tglw.trace import rules as lang

ACTIONS = ("list", "add", "change", "delete", "on", "off", "undo", "reset", "all-off", "history", "preview", "help")
BY = "agent"
SUGGEST_READINGS = 100  # the latest readings suggestions are learned from
PREVIEW_READINGS = 5  # readings each suggestion is previewed on
SUGGESTIONS_SHOWN = 5
_SUGGESTION = re.compile(r"^s-[0-9a-f]{4}$")


class Refused(ValueError):
    """What the agent gets back as the call's error."""


def run(action: str, text: str = "", why: str = "") -> str:
    from tglw.store import rules as store

    a = action.strip().lower().replace("_", "-")
    t = text.strip()
    try:
        if a == "help":
            return help_text()
        if a == "list":
            return listing(store.load()) + suggested()
        if a == "add" and _SUGGESTION.match(t):
            g = _suggestion(t)
            types = store.load().types
            if g.typedef and lang.type_name(g.typedef) not in types:
                store.add(g.typedef, BY, why or f"system suggestion {g.id}, accepted by the user")
            rid = store.add(g.rule, BY, why or f"system suggestion {g.id}, accepted by the user", suggested=True)
            return f"added {rid}: {g.rule}" + (f" (with {g.typedef})" if g.typedef else "")
        if a == "add":
            rid = store.add(_need(t, "the rule, e.g. " + lang.EXAMPLE), BY, why)
            return f"added {rid}: {t}"
        if a == "change":
            rid, sep, new = t.partition(":")
            if not sep or not new.strip():
                raise Refused("change needs text 'ID: new rule', e.g. r-7f3a: " + lang.EXAMPLE)
            got = store.change(rid.strip(), new.strip(), BY, why)
            note = f" (recommended {rid.strip()} is switched off; your version is {got})" if got != rid.strip() else ""
            return f"changed {got}: {new.strip()}{note}"
        if a == "delete":
            store.delete(_need(t, "the id, e.g. r-7f3a"), BY, why)
            return f"deleted {t}" + (" (a recommended rule: switched off)" if t.startswith("rec-") else "")
        if a in ("on", "off") and t.lower() == "all" or a == "all-off":
            store.all_off(BY, why, off=a != "on")
            return "all rules off: the tools work as without rules" if a != "on" else "rules on again"
        if a == "off" and _SUGGESTION.match(t):
            g = _suggestion(t)
            store.decline(g.rule, BY, why)
            return f"{g.id} turned down: it will not be suggested again (undo brings it back)"
        if a in ("on", "off"):
            store.set_on(_need(t, "the id, e.g. r-7f3a"), a == "on", BY, why)
            return f"{t} {'on' if a == 'on' else 'off'}"
        if a == "undo":
            return store.undo(BY, why)
        if a == "reset":
            store.reset(BY, why)
            return "factory rules restored: your rules and changes removed (undo brings them back)\n" + listing(store.load())
        if a == "history":
            return history(store.history(limit=20))
        if a == "preview":
            from . import preview
            return preview.show(_texts(_need(t, "the rule (or its id) to preview"), store))
    except (lang.RuleError, store.StoreError) as err:
        raise Refused(str(err)) from err
    raise Refused(f"unknown action {action!r}; actions: {', '.join(ACTIONS)}")


def suggestions() -> tuple[list, list[dict]]:
    """Suggestions from this machine's latest readings (task LANG's learn; the store's rules in effect and turned-down
    suggestions left out), and the readings their indexes refer to."""
    from tglw.store import rules as store
    from tglw.trace import events as trace_events
    from tglw.trace import learn

    from . import preview
    found, traces = [], []
    for r in preview.readings(last=SUGGEST_READINGS):
        t = trace_events.traces_of(r["calls"])
        if t:
            found.append(r)
            traces.append(t[0])
    return learn.suggest(traces, rules=[e.text for e in store.effective()], suppressed=store.suppressed()), found


def _suggestion(sid: str) -> object:
    g = next((g for g in suggestions()[0] if g.id == sid), None)
    if g is None:
        raise Refused(f"no suggestion {sid} now: list shows the current ones")
    return g


def suggested() -> str:
    """The end of a list: what the readings suggest (not active), each with its evidence and a preview on the
    readings it came from. Empty when there is nothing to suggest, or it cannot be worked out."""
    from . import preview
    try:
        found_suggestions, found = suggestions()
        lines = []
        for g in found_suggestions[:SUGGESTIONS_SHOWN]:
            texts = ([g.typedef] if g.typedef else []) + [g.rule]
            e = preview.effect(texts, [found[i] for i in g.readings[:PREVIEW_READINGS]])
            added = f"{e['tokens'] / 1000:+.1f}k" if abs(e["tokens"]) >= 1000 else f"{e['tokens']:+}"
            lines += [f"  {g.id}  {g.rule}" + (f"     ({g.typedef})" if g.typedef else ""), f"      why: {g.why}",
                      f"      preview: acts on {e['acted']} steps · {added} tokens · {e['reread']} later steps read what it added"]
    except Exception:  # noqa: BLE001 - suggestions are extra: a list without them is still the list
        return ""
    if not lines:
        return ""
    return ("\n\nsuggested from your readings (not active):\n" + "\n".join(lines)
            + "\n  (add s-… adds one; off s-…: not suggested again)")


def _need(text: str, what: str) -> str:
    if not text:
        raise Refused(f"text is empty: give {what}")
    return text


def _texts(text: str, store: object) -> list[str]:
    """What preview runs: stored rules named by id (with the types in force), or rules written out, one per line."""
    rs = store.load()  # type: ignore[attr-defined]
    by_id = {e.id: e for e in rs.entries}
    out = [by_id[x].text if x in by_id else x for x in (line.strip() for line in text.splitlines()) if x]
    types = [f"type {n} = {', '.join(p)}" for n, p in rs.types.items()]
    named = {lang.type_name(x) for x in out if x.startswith("type")}
    return [t for t in types if t.split()[1] not in named] + out


def listing(rs: object) -> str:
    """Every rule and type, in effect or not: id, version, layer and state, the text, where it came from, and why
    one does not parse."""
    entries = list(rs.entries)  # type: ignore[attr-defined]
    on = sum(1 for e in entries if e.on and e.parsed is not None) if not rs.all_off else 0  # type: ignore[attr-defined]
    head = f"rules: {on} in effect" + (" · all off (as without rules)" if rs.all_off else "")  # type: ignore[attr-defined]
    lines = [head]
    for e in entries:
        src = e.source or {}
        when = time.strftime("%Y-%m-%d", time.localtime(src["at"])) if src.get("at") else ""
        whose = "recommended" if e.layer == "recommended" else "system suggestion, accepted by the user " + when \
            if src.get("how") else f"by {src.get('by', '?')} {when}".strip()
        why = f' why "{src["why"]}"' if src.get("why") and e.layer != "recommended" else ""
        state = e.state + (" · newer recommended version" if e.newer_shipped else "")
        lines.append(f"{e.id} v{e.ver} · {whose} · {state}: {e.text}{why}" + (f"\n  error: {e.error}" if e.error else ""))
    return "\n".join(lines)


def history(rows: list[dict]) -> str:
    if not rows:
        return "no changes yet"
    out = []
    for r in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])) if r.get("at") else ""
        out.append(f"{r.get('n')} · {when} · {r.get('op')}" + (f" {r['id']}" if r.get("id") else "")
                   + (f" (undoes {r['undoes']})" if r.get("undoes") else "") + (f' · why "{r["why"]}"' if r.get("why") else ""))
    return "\n".join(out)


def quick(err: Exception) -> str:
    """For a rule written wrong (not a change the store refused): the quick reference -- each signal, what it means
    and the actions it takes (task LANG's RULES_QUICK; until there is one, the signals and their actions)."""
    if not isinstance(err.__cause__, lang.RuleError):
        return ""
    from tglw.trace import lang as words
    text = getattr(words, "RULES_QUICK", None)
    if text:
        return text.strip()
    return "signals and their actions:\n" + "\n".join(f"  {sig}: {', '.join(acts)}" for sig, acts in lang.ACTIONS.items())


def help_text() -> str:
    """The rule-writing part of the language (task LANG's), and how this tool takes it."""
    from tglw.trace import lang as words
    text = getattr(words, "RULES_HELP", None) or words.HELP[words.HELP.index("Rules"):]
    return (text.rstrip() + "\n\nThis tool: action=list | add | change | delete | on | off | undo | reset | all-off | history | "
            "preview | help; text = the rule, or its id (change: 'ID: new rule'; on/off: an id, or 'all'); why = what the "
            "user asked for. preview replays your last readings with and without the rule.")
