"""A trace as text in the reading language: one line per step, then where it stopped and what it ignored."""

from __future__ import annotations

from typing import Any

from .model import Step, Trace

TOP_PAGES = 3


def pages_text(pages: list[int]) -> str:
    """[117, 118, 119, 149] -> 'p117-119 p149'."""
    runs: list[list[int]] = []
    for p in sorted(set(pages)):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return " ".join(f"p{a}" if a == b else f"p{a}-{b}" for a, b in runs)


def _tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 10_000 else str(n)


def _what(s: Step) -> str:
    c, a = s.call, s.call.args
    if c.block in ("find", "grep"):
        extra = "".join(f" {k}={a[k]}" for k in ("k", "unit", "offset") if k in a and a[k] not in (None, "", 0, "block"))
        q = a.get("query")
        q = f'"{q}"' if isinstance(q, str) else f"(query {q.get('len', '?')} chars)" if isinstance(q, dict) else '""'
        return f"{c.block} {q}{extra}"
    if c.block == "read":
        return "read " + " ".join(c.addrs) + (f" max={a['max_tokens']}" if a.get("max_tokens") else "")
    if c.block == "view":
        return f"view {' '.join(c.addrs)}" + (f" x{a['scale']}" if a.get("scale") else "")
    kind = {"outline": "outline", "context": "context", "list_documents": "documents", "region": "preview"}[c.tool]
    return f"map {kind}" + (f" {' '.join(c.addrs)}" if c.addrs else "")


def _origin(s: Step) -> str:
    if not s.origin:
        return ""
    if s.origin == "hit":  # rank 0: an identifier match the tool added to the search
        return f" <- hit {s.origin_step}#{s.origin_rank or 'id'}"
    if s.origin in ("continues", "cut", "map"):
        return f" <- {s.origin} {s.origin_step}"
    return f" <- {s.origin}"


def _result(s: Step) -> str:
    c = s.call
    if c.error:
        return f"error {c.error}"
    if c.block in ("find", "grep"):
        if any(x["k"] == "zero" for x in c.signals):
            return "zero"
        pages = list(dict.fromkeys(p for h in c.hits if h["r"] for p in h["p"]))[:TOP_PAGES]
        head = f"count {c.count}" if c.block == "grep" and c.count is not None else f"hits {len([h for h in c.hits if h['r']])}"
        return " ".join([head] + (["on"] + [f"p{p}" for p in pages] if pages else []))  # where hits are: not read
    if c.block == "view":
        return f"image {pages_text(c.pages)}" if c.pages else "image"
    if c.block == "read":
        text = pages_text(c.pages) if c.pages else "nothing"
        return text + (" (" + ", ".join(f"bad address '{e['in']}'" for e in c.addr_errors) + ")" if c.addr_errors else "")
    if c.listed:
        return f"{len(c.listed)} listed" + (f" {pages_text(c.pages)}" if c.pages else "")
    return pages_text(c.pages) if c.pages else "ok"


def _signal(x: dict[str, Any]) -> str | None:
    k = x["k"]
    if k == "continues":
        return f"continues p{x['page']}" if x.get("page") else None
    if k == "cut":
        rest = x.get("rest") or []
        return f"cut next {rest[0]}{'+' if len(rest) > 1 else ''}" if rest else "cut"
    if k in ("scan", "qc"):
        return f"{k} {pages_text(x.get('pages', []))}"
    if k in ("figure", "table"):
        return f"{k} {' '.join(x.get('units', [])[:3])}"
    if k in ("saturated", "too-long"):
        return k
    return None  # hits, count and zero are in the result


def _said(key: Any) -> str:
    return f'"{key}"' if isinstance(key, str) else f"(key {key.get('len', '?')} chars)" if isinstance(key, dict) else '""'


def _counts(d: dict[str, Any]) -> str:
    """' "SI-4" 78, "AU-6(2)" 12' from keys [{key, n}]; ' "SI-4" 78' from q (or keys as names) and n; ' 78' from n."""
    keys = d.get("keys")
    if isinstance(keys, list) and keys and all(isinstance(x, dict) for x in keys):
        return " " + ", ".join(f"{_said(x.get('key'))} {x.get('n')}" for x in keys)
    names = [d["q"]] if d.get("q") else [x for x in keys if isinstance(x, str)] if isinstance(keys, list) else []
    return "".join(f' "{x}"' for x in names) + (f" {d['n']}" if d.get("n") is not None else "")


def _rule_did(d: dict[str, Any]) -> str:
    """'add next-page +p151', 'add count "SI-4" 78', 'add next-page held back p152 (budget)' (LANG_SPEC 11.8)."""
    parts = [str(d.get("act", ""))]
    if d.get("pages"):
        parts.append("+" + pages_text(d["pages"]).replace(" ", " +"))
    if d.get("first"):
        parts.append(f"p{d['first']}")
    if listed := d.get("listed"):
        parts.append(" ".join(listed[:3]) + (f" +{len(listed) - 3}" if len(listed) > 3 else ""))
    if d.get("keys"):
        parts.append(", ".join(f"{_said(x.get('key'))} {x.get('n')}" for x in d["keys"] if isinstance(x, dict)))
    if d.get("target"):
        parts.append(str(d["target"]))
    parts += [f"held back {x.get('what')} ({x.get('why')})" for x in d.get("skipped") or [] if isinstance(x, dict)]
    if d.get("err"):
        parts.append("error")
    return " ".join(p for p in parts if p)


def _decision(d: dict[str, Any]) -> str:
    who = f"rule {d.get('src')}" if d.get("by") == "rule" else "tool"
    k = d["k"]
    if k in ("adjacent", "grow") and d.get("pages"):
        what = "+" + pages_text(d["pages"]).replace(" ", " +") + f" ({k})"
    elif k in ("exact_note", "ident_hits"):
        what = "+count" + _counts(d)
    elif k == "auto_zoom":
        what = f"zoom x{d.get('scale')}"
    elif k == "rule":
        what = _rule_did(d)
    else:
        what = k.replace("_", "-")
    return f"{who} {what}" + (f" {_tokens(d['tokens'])}t" if d.get("tokens") else "")


def line(s: Step) -> str:
    c = s.call
    parts = [f"{s.label:<3} {_what(s)}{_origin(s)} -> {_result(s)}"]
    parts += [t for x in c.signals if (t := _signal(x))
              and not (x["k"] == "continues" and x.get("page") in c.pages)]  # points into what it already shows
    parts += [_decision(d) for d in c.decisions]
    cost = f"{_tokens(c.tokens)}t" + (f" +{c.images} img" if c.images else "")
    return "; ".join(parts) + f" · {cost}"


def render(trace: Trace, ignored_only: bool = False) -> str:
    out = [] if ignored_only else [line(s) for s in trace.steps]
    if trace.steps:
        last = trace.steps[-1].label
        secs = list(dict.fromkeys(u for st in trace.steps if st.call.block in ("read", "map")
                                  for u in st.call.units if u.startswith("s")))
        sections = f" · sections {' '.join(secs[:8])}{' …' if len(secs) > 8 else ''}" if secs else ""
        out.append(f"stop after {last} · read {pages_text(trace.pages) or '-'}{sections} · {_tokens(trace.tokens)} tokens")
    if trace.ignored:
        out.append("ignored: " + "; ".join(f"{x['k']}{' ' + x['target'] if x['target'] else ''} ({x['step']})"
                                         for x in trace.ignored))
    elif ignored_only:
        out.append("ignored: none")
    if trace.rules and not ignored_only:
        out.append("rules changed: " + "; ".join(
            f"{x.get('action') or '?'}" + (f" {str(x['text'])[:60]}" if x.get("text") else "")
            + (" (refused)" if x.get("error") else "") + (f" after {x['after']}" if x.get("after") else " before 1")
            for x in trace.rules))
    if trace.lost:
        out.append("lost reports: " + ", ".join(f"{k} {v}" for k, v in sorted(trace.lost.items())))
    if trace.answer is not None and not ignored_only:
        cites = pages_text(trace.answer["cites"])
        out.append(f"answer (from the log) {trace.answer['chars']} chars" + (f" · cites {cites}" if cites else ""))
    return "\n".join(out)


def preview(pairs: list[tuple[Trace, Trace]]) -> str:
    """What rules would change in past readings (LANG_SPEC 11.12): each reading replayed with the rules off and on.
    A step a rule acts on is shown as it was, then what each rule added; a later step that only read what a rule
    had added is marked; the last line sums it up."""
    out = [f"preview: {len(pairs)} reading{'s' if len(pairs) != 1 else ''}"]
    acted = changed = tokens = held = reread = 0
    for n, (off, on) in enumerate(pairs, 1):
        head = f"reading {n} · doc {off.doc or '-'} · {len(off.steps)} steps"
        body: list[str] = []
        added_at: dict[int, str] = {}  # page -> step label of the rule that added it
        shown: set[int] = set()
        for s_off, s_on in zip(off.steps, on.steps):
            rules = [d for d in s_on.call.decisions if d.get("by") == "rule"]
            new = set(s_off.call.pages) - shown
            mark = ""
            if s_off.call.block == "read" and new and all(p in added_at for p in new):
                mark = f" · already added at {', '.join(sorted({added_at[p] for p in new}))}"
                reread += 1
            if rules or mark:
                body.append(f"  {line(s_off)}{mark}")
            for d in rules:
                body.append(f"      + {_decision(d)}")
                tokens += int(d.get("tokens") or 0)
                held += len(d.get("skipped") or [])
                for p in d.get("pages") or []:
                    added_at.setdefault(p, s_on.label)
            rule_pages = {p for d in rules for p in d.get("pages") or []}
            if rules and sorted(set(s_on.call.pages) - rule_pages) != sorted(s_off.call.pages):
                body.append(f"      ~ its own return changed: {pages_text(s_off.call.pages) or '-'} -> "
                            f"{pages_text(sorted(set(s_on.call.pages) - rule_pages)) or '-'}")
            acted += bool(rules)
            shown |= set(s_off.call.pages)
        changed += any(d.get("by") == "rule" for s in on.steps for d in s.call.decisions)
        out.append(head + (": unchanged" if not body else ""))
        out += body
    out.append(f"total: rules act on {acted} step{'s' if acted != 1 else ''} in {changed} of {len(pairs)} readings"
               f" · +{_tokens(tokens)} tokens" + (f" · {held} held back" if held else "")
               + (f" · {reread} later step{'s' if reread != 1 else ''} read what they added" if reread else ""))
    return "\n".join(out)
