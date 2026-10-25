"""Rules and type definitions of the reading language: parse, check, and say what is wrong and how to fix it.

    on SIGNAL [where COND [and COND]...] in SCOPE do ACTION[, ACTION] [max N] [budget Nt] [why "..."]
    type NAME = PATTERN[, PATTERN]...

A rule acts: the tool carries it out and the trace shows it. There is no action that only advises the model.
Rules are kept as text; this module is the only reader of that text.
"""

from __future__ import annotations

import difflib
import fnmatch
import re
from dataclasses import dataclass, field

# SIGNAL -> the actions that fit it
ACTIONS = {
    "read": ("widen", "add next-page", "add prev-page", "add image"),
    "continues": ("add next-page", "add first-line"),
    "cut": ("add rest", "add unread-headings"),
    "zero": ("add absence",),
    "saturated": ("add coverage",),
    "view": ("zoom",),
}
SIGNALS = tuple(ACTIONS)
CONDS = ("same-section", "from-hit", "scan", "figure", "table", "has-id", "pages", "tokens", "searches")
SIZE_CONDS = ("pages", "tokens", "searches")  # searches: how many searches this reading has made, this one included
ADDITIONS = ("next-page", "prev-page", "first-line", "unread-headings", "rest", "absence", "image", "coverage")
# in traces, but no rule starts on them: nothing a rule could add there (v1 already counts a query's identifiers)
NOT_TRIGGERS = ("find", "grep", "map", "hits", "count")
IMAGE_CONDS = ("scan", "figure", "table")  # what is on the pages shown; add image takes the first such image
ADVICE = ("remind", "hint", "suggest", "tell", "say", "warn", "ask", "prefer", "should")
MAX_LIMIT, BUDGET_LIMIT, WIDEN_LIMIT, ZOOM_LIMIT = 20, 20_000, 3, 3.0
# what each signal means, in plain words (errors and the quick reference list them: models copy what errors say)
GLOSS = {"read": "a read returned text", "continues": "the page says its section goes on",
         "cut": "a read stopped at its budget; next: lists the rest", "zero": "a search found nothing",
         "saturated": "searches keep returning hits seen before", "view": "an image came back"}
# the smallest rule of each signal: a fix example carries no max or budget the rule did not have
FIRST = {"read": "widen 1", "continues": "add next-page", "cut": "add rest", "zero": "add absence",
         "saturated": "add coverage", "view": "zoom x2"}
EXAMPLES = {s: f"on {s} in all do {a}" for s, a in FIRST.items()}
EXAMPLE = EXAMPLES["continues"]
SIGNAL_LIST = ", ".join(f"{s} ({GLOSS[s]})" for s in ACTIONS)
# words models write for a signal (gate 3: "truncated" read as a near spelling of "saturated" misled)
SIGNAL_WORDS = {"truncated": "cut", "truncate": "cut", "truncation": "cut", "cutoff": "cut", "cut-off": "cut",
                "next": "cut", "continue": "continues", "continued": "continues", "nothing": "zero", "empty": "zero",
                "none": "zero", "no-hits": "zero", "notfound": "zero", "not-found": "zero", "repeat": "saturated",
                "repeated": "saturated", "repeats": "saturated", "image": "view", "picture": "view", "region": "view"}
_TOKEN = re.compile(r'"[^"]*"|<=|>=|[<>=,]|[^\s,<>="]+')
_NAME = re.compile(r"[a-z][a-z0-9-]*")
_PATTERN = re.compile(r"[A-Za-z0-9_.*?\-\[\]]+")


class RuleError(ValueError):
    """Where a rule is wrong (1-based column), what is wrong, and a corrected example."""

    def __init__(self, col: int, msg: str, fix: str):
        self.col, self.msg, self.fix = col, msg, fix
        super().__init__(f"col {col}: {msg} -- e.g. {fix}")


@dataclass(frozen=True)
class Cond:
    name: str
    op: str | None = None
    value: int | None = None

    def text(self) -> str:
        return f"{self.name}{self.op}{self.value}" if self.op else self.name


@dataclass(frozen=True)
class Action:
    verb: str           # add, widen, zoom
    arg: str            # next-page, ... / pages for widen / scale for zoom

    def text(self) -> str:
        return {"add": f"add {self.arg}", "widen": f"widen {self.arg}", "zoom": f"zoom x{self.arg}"}[self.verb]


@dataclass(frozen=True)
class Rule:
    signal: str
    conds: tuple[Cond, ...]
    scope: tuple[str, str | None]       # ("all", None) | ("doc", name) | ("type", name)
    actions: tuple[Action, ...]
    max: int | None = None
    budget: int | None = None
    why: str | None = None

    def text(self) -> str:
        where = (" where " + " and ".join(c.text() for c in self.conds)) if self.conds else ""
        scope = "all" if self.scope[0] == "all" else f"{self.scope[0]} {self.scope[1]}"
        tail = (f" max {self.max}" if self.max else "") + (f" budget {self.budget}t" if self.budget else "") + \
            (f' why "{self.why}"' if self.why else "")
        return f"on {self.signal}{where} in {scope} do {', '.join(a.text() for a in self.actions)}{tail}"

    def applies_to(self, doc: str, types: dict[str, tuple[str, ...]]) -> bool:
        kind, name = self.scope
        if kind == "all":
            return True
        if kind == "doc":
            return doc == name
        return any(fnmatch.fnmatchcase(doc, p) for p in types.get(name or "", ()))


@dataclass(frozen=True)
class TypeDef:
    name: str
    patterns: tuple[str, ...]

    def text(self) -> str:
        return f"type {self.name} = {', '.join(self.patterns)}"


@dataclass
class _Tokens:
    text: str
    items: list[tuple[str, int]] = field(default_factory=list)
    i: int = 0
    signal: str | None = None                      # what the rule has said right so far, for its fix example
    conds: list[Cond] = field(default_factory=list)
    scope: tuple[str, str | None] | None = None
    actions: list[Action] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.items = [(m.group(0), m.start() + 1) for m in _TOKEN.finditer(self.text)]

    def peek(self) -> str | None:
        return self.items[self.i][0] if self.i < len(self.items) else None

    def col(self) -> int:
        return self.items[self.i][1] if self.i < len(self.items) else len(self.text) + 1

    def take(self) -> str:
        tok = self.items[self.i][0]
        self.i += 1
        return tok


def _fits(c: Cond, signal: str) -> bool:
    return not ((c.name == "same-section" and signal not in ("continues", "cut")) or (c.name == "from-hit" and signal != "read")
                or (c.name == "has-id" and signal != "zero") or (c.name in IMAGE_CONDS and signal not in ("read", "view")))


def _acts_in(text: str) -> list[str]:
    return [a for a in dict.fromkeys(x for xs in ACTIONS.values() for x in xs) if re.search(rf"\b{a}\b", text)]


def _signal_of(text: str) -> str | None:
    """The one signal the action written in the rule goes with ('add rest' -> cut), if only one."""
    sigs = {s for s, xs in ACTIONS.items() for a in _acts_in(text) if a in xs}
    return sigs.pop() if len(sigs) == 1 else None


def _meant(word: str, text: str) -> str | None:
    """The signal a wrong signal word most likely meant: by the action the rule wrote, by a common word for it,
    then by spelling."""
    if s := _signal_of(text):
        return s
    if word.lower() in SIGNAL_WORDS:
        return SIGNAL_WORDS[word.lower()]
    near = difflib.get_close_matches(word, list(SIGNALS), n=1, cutoff=0.6)
    return near[0] if near else None


_SCOPE_IN = re.compile(r"\bin\s+(all|doc\s+[^\s,]+|type\s+[a-z][a-z0-9-]*)\s+do\b")
_SIZE_IN = re.compile(r"\b(pages|tokens|searches)\s*(<=|>=|<|>)\s*(\d+)\b")


def _conds_in(text: str) -> list[Cond]:
    """The conditions a rule wrote between 'where' and 'in', when its parse stopped before them."""
    m = re.search(r"\bwhere\s+(.*?)\s+in\s", text)
    if not m:
        return []
    out = [Cond(w) for w in re.findall(r"[a-z-]+", m.group(1)) if w in CONDS and w not in SIZE_CONDS]
    return out + [Cond(n, op, int(v)) for n, op, v in _SIZE_IN.findall(m.group(1))]


def _fix(t: _Tokens, signal: str | None = None, conds: list[Cond] | None = None, action: str | None = None,
         scope: bool = True) -> str:
    """A corrected example made of what the rule said right, the smallest action for its signal, and its own max
    and budget if it wrote them (models copy the example: it must not add what the user did not ask for, nor widen
    the documents it is for)."""
    sig = signal or t.signal or _signal_of(t.text) or "continues"
    cs = [c for c in (t.conds if conds is not None or t.signal else _conds_in(t.text)) if _fits(c, sig)] \
        if conds is None else [c for c in conds if _fits(c, sig)]
    if t.scope is None and scope and (m := _SCOPE_IN.search(t.text)):
        t.scope = ("all", None) if m.group(1) == "all" else tuple(m.group(1).split(None, 1))  # type: ignore[assignment]
    act = action or (", ".join(a.text() for a in t.actions if (f"add {a.arg}" if a.verb == "add" else a.verb) in ACTIONS[sig])
                     or next((a for a in _acts_in(t.text) if a in ACTIONS[sig] and a.startswith("add")), None)
                     or FIRST[sig])
    if "add image" in act and not any(c.name in IMAGE_CONDS for c in cs):
        cs.append(Cond(next((w for w in IMAGE_CONDS if re.search(rf"\b{w}", t.text)), "figure")))
    where = f" where {' and '.join(c.text() for c in cs)}" if cs else ""
    where_to = "all" if not (scope and t.scope) or t.scope[0] == "all" else f"{t.scope[0]} {t.scope[1]}"
    tail = ""
    if (m := re.search(r"\bmax\s+(\d+)\b", t.text)) and 1 <= int(m.group(1)) <= MAX_LIMIT:
        tail += f" max {int(m.group(1))}"
    if (m := re.search(r"\bbudget\s+(\d+)t\b", t.text)) and 1 <= int(m.group(1)) <= BUDGET_LIMIT:
        tail += f" budget {int(m.group(1))}t"
    return f"on {sig}{where} in {where_to} do {act}{tail}"


def _near(word: str, options: tuple[str, ...] | list[str]) -> str:
    m = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return f" (did you mean '{m[0]}'?)" if m else ""


def _expect(t: _Tokens, word: str, before: str, fix: str) -> None:
    got = t.peek()
    if got != word:
        where = f"'{got}'" if got else "the end"
        raise RuleError(t.col(), f"expected '{word}' {before}, found {where}", fix)
    t.take()


def _int(t: _Tokens, what: str, fix: str, suffix: str = "") -> int:
    tok = t.peek()
    col = t.col()
    if tok is None:
        raise RuleError(col, f"{what} needs a number", fix)
    t.take()
    body = tok[:-len(suffix)] if suffix and tok.endswith(suffix) else tok
    if suffix and not tok.endswith(suffix):
        raise RuleError(col, f"{what} is a token count: write it with '{suffix}', as {tok}{suffix}", fix)
    if not body.isdigit():
        raise RuleError(col, f"{what} needs a whole number, found '{tok}'", fix)
    return int(body)


def _cond(t: _Tokens, signal: str) -> Cond:
    if t.peek() is None:
        raise RuleError(t.col(), f"'where' needs a condition: {', '.join(CONDS)}", _fix(t))
    col, word = t.col(), t.take()
    if word not in CONDS:
        raise RuleError(col, f"unknown condition '{word}'{_near(word, CONDS)}; conditions: {', '.join(CONDS)}", _fix(t))
    if word in SIZE_CONDS:
        op = t.peek()
        if op not in ("<=", ">=", "<", ">"):
            raise RuleError(t.col(), f"'{word}' needs a comparison, as {word}{'>=5' if word == 'searches' else '<=2'}", _fix(t))
        t.take()
        return Cond(word, op, _int(t, word, _fix(t)))
    cond = Cond(word)
    if not _fits(cond, signal):
        owner = {"same-section": "continues or cut", "from-hit": "read", "has-id": "zero"}.get(word, "read or view")
        to = {"same-section": "continues", "from-hit": "read", "has-id": "zero"}.get(word, "read")
        raise RuleError(col, f"{word} belongs to {owner}", _fix(t, signal=to, conds=t.conds + [cond]))
    return cond


def _scope(t: _Tokens, signal: str, types: dict[str, tuple[str, ...]] | None) -> tuple[str, str | None]:
    col, word = t.col(), t.peek()
    if word is None or word == "do":
        raise RuleError(col, "every rule says which documents it is for: 'in all', 'in doc NAME' or 'in type NAME'", _fix(t))
    t.take()
    if word == "all":
        return "all", None
    if word not in ("doc", "type"):
        raise RuleError(col, f"scope is 'all', 'doc NAME' or 'type NAME', found '{word}'{_near(word, ('all', 'doc', 'type'))}", _fix(t))
    name_col, name = t.col(), t.peek()
    if name is None or name == "do":
        t.scope = (word, "hkex" if word == "type" else "my__doc")
        raise RuleError(name_col, f"'{word}' needs a name", _fix(t))
    t.take()
    if word == "type":
        if not _NAME.fullmatch(name):
            raise RuleError(name_col, f"type names are lowercase letters, digits and '-', found '{name}'", "type hkex = hkex__*")
        if types is not None and name not in types:
            raise RuleError(name_col, f"no type '{name}'{_near(name, list(types))}; define it first: type {name} = PATTERN", f"type {name} = {name}__*")
    return word, name


def _action(t: _Tokens, signal: str) -> Action:
    allowed = ACTIONS[signal]
    says = f"for {signal} ({GLOSS[signal]}): {', '.join(allowed)}"
    col, verb = t.col(), t.peek()
    if verb is None:
        raise RuleError(col, f"'do' needs an action; {says}", _fix(t))
    t.take()
    if verb in ADVICE:
        raise RuleError(col, f"rules act, they do not '{verb}': the tool does the action itself; {says}", _fix(t))
    if verb == "add":
        acol, what = t.col(), t.peek()
        if what is None:
            raise RuleError(acol, f"'add' needs what to add; {says}", _fix(t))
        t.take()
        if what not in ADDITIONS and f"{what}-{t.peek()}" in ADDITIONS:  # "add first line"
            raise RuleError(acol, f"write it with a hyphen: add {what}-{t.peek()}; {says}", _fix(t, action=f"add {what}-{t.peek()}"))
        if what not in ADDITIONS:
            raise RuleError(acol, f"cannot add '{what}'{_near(what, ADDITIONS)}; {says}", _fix(t))
        action = Action("add", what)
    elif verb == "widen":
        n = _int(t, "widen", _fix(t, signal="read", action="widen 1"))
        if not 1 <= n <= WIDEN_LIMIT:
            raise RuleError(col, f"widen adds 1 to {WIDEN_LIMIT} pages each side, found {n}", _fix(t, signal="read", action="widen 1"))
        action = Action("widen", str(n))
    elif verb == "zoom":
        zcol, z = t.col(), t.peek()
        if z is None:
            raise RuleError(zcol, "zoom needs a scale, as x2", _fix(t, signal="view", action="zoom x2"))
        t.take()
        try:
            scale = float(z[1:]) if z.startswith("x") else None
        except ValueError:
            scale = None
        if scale is None or not 1.0 < scale <= ZOOM_LIMIT:
            raise RuleError(zcol, f"zoom is x followed by a scale above 1 and at most {ZOOM_LIMIT:g}, found '{z}'",
                            _fix(t, signal="view", action="zoom x2"))
        action = Action("zoom", f"{scale:g}")
    else:
        raise RuleError(col, f"unknown action '{verb}'{_near(verb, ('add', 'widen', 'zoom'))}; {says}", _fix(t))
    key = f"add {action.arg}" if action.verb == "add" else action.verb
    if key not in allowed:
        goes = [sg for sg, xs in ACTIONS.items() if key in xs]
        if len(goes) == 1:  # it can only mean that signal: the fix moves the rule there
            raise RuleError(col, f"'{action.text()}' does not fit '{signal}'; {says}; '{action.text()}' goes with "
                                 f"{goes[0]} ({GLOSS[goes[0]]})", _fix(t, signal=goes[0], action=action.text()))
        raise RuleError(col, f"'{action.text()}' does not fit '{signal}'; {says}", _fix(t))
    return action


def _rule(t: _Tokens, types: dict[str, tuple[str, ...]] | None) -> Rule:
    t.take()  # on
    col, signal = t.col(), t.peek()
    if signal is None:
        raise RuleError(col, f"'on' needs a signal: {SIGNAL_LIST}", _fix(t))
    t.take()
    if signal in NOT_TRIGGERS:
        raise RuleError(col, f"'{signal}' is in traces but starts no rule (nothing to add there); signals: {SIGNAL_LIST}", _fix(t))
    if signal not in SIGNALS:
        meant = _meant(signal, t.text)
        raise RuleError(col, f"unknown signal '{signal}'" + (f" (did you mean '{meant}'?)" if meant else "")
                        + f"; signals: {SIGNAL_LIST}", _fix(t, signal=meant))
    t.signal = signal
    if t.peek() == "where":
        t.take()
        t.conds.append(_cond(t, signal))
        while t.peek() == "and":
            t.take()
            t.conds.append(_cond(t, signal))
    if t.peek() == "do":
        raise RuleError(t.col(), "every rule says which documents it is for: 'in all', 'in doc NAME' or 'in type NAME' before 'do'",
                        _fix(t))
    _expect(t, "in", "before the documents it is for", _fix(t))
    t.scope = _scope(t, signal, types)
    _expect(t, "do", "before the action", _fix(t))
    while True:
        col = t.col()
        t.actions.append(_action(t, signal))
        if t.actions[-1] == Action("add", "image") and not any(c.name in IMAGE_CONDS for c in t.conds):
            raise RuleError(col, "add image needs 'where scan', 'where figure' or 'where table': it says which image", _fix(t))
        if t.peek() != ",":
            break
        t.take()
    mx = budget = None
    why = None
    while (word := t.peek()) is not None:
        col = t.col()
        t.take()
        if word == "max" and mx is None:
            mx = _int(t, "max", _fix(t))
            if not 1 <= mx <= MAX_LIMIT:
                raise RuleError(col, f"max is 1 to {MAX_LIMIT} times per reading, found {mx}", _fix(t))
        elif word == "budget" and budget is None:
            budget = _int(t, "budget", _fix(t), suffix="t")
            if not 1 <= budget <= BUDGET_LIMIT:
                raise RuleError(col, f"budget is 1 to {BUDGET_LIMIT} tokens a call, found {budget}", _fix(t))
        elif word == "why" and why is None:
            q = t.peek()
            if q is None or not q.startswith('"'):
                raise RuleError(t.col(), 'why takes a quoted reason, as why "lists run over pages"', _fix(t) + ' why "lists run over pages"')
            why = t.take()[1:-1]
        elif word in ("max", "budget", "why"):
            raise RuleError(col, f"'{word}' given twice", _fix(t))
        else:
            raise RuleError(col, f"unexpected '{word}' after the action{_near(word, ('max', 'budget', 'why'))}; after it come max N, budget Nt, why \"...\"", _fix(t))
    return Rule(signal, tuple(t.conds), t.scope, tuple(t.actions), mx, budget, why)


def _typedef(t: _Tokens) -> TypeDef:
    t.take()  # type
    col, name = t.col(), t.peek()
    if name is None or not _NAME.fullmatch(name):
        raise RuleError(col, f"type needs a name of lowercase letters, digits and '-', found {repr(name) if name else 'nothing'}", "type hkex = hkex__*")
    t.take()
    _expect(t, "=", "after the type name", f"type {name} = {name}__*")
    patterns = []
    while True:
        pcol, p = t.col(), t.peek()
        if p is None:
            raise RuleError(pcol, "type needs at least one document-name pattern after '='", f"type {name} = {name}__*")
        t.take()
        if not _PATTERN.fullmatch(p):
            raise RuleError(pcol, f"'{p}' is not a document-name pattern (letters, digits, _ . - and * ? [ ])", f"type {name} = {name}__*")
        patterns.append(p)
        if t.peek() != ",":
            break
        t.take()
    if t.peek() is not None:
        raise RuleError(t.col(), f"unexpected '{t.peek()}' after the patterns; separate patterns with ','", f"type {name} = {', '.join(patterns)}, other__*")
    return TypeDef(name, tuple(patterns))


def parse(text: str, types: dict[str, tuple[str, ...]] | None = None) -> Rule | TypeDef:
    """A rule or a type definition; RuleError says where it is wrong and gives a corrected example.
    With `types` (name -> patterns) given, a rule naming an unknown type is an error too."""
    t = _Tokens(text.strip())
    first = t.peek()
    if first == "on":
        return _rule(t, types)
    if first == "type":
        return _typedef(t)
    word = first or ""
    raise RuleError(1, f"a rule starts with 'on', a type with 'type', found {repr(word) if word else 'nothing'}{_near(word, ('on', 'type'))}",
                    _fix(t))


def check(text: str, types: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """None when the text parses; else the error as one line."""
    try:
        parse(text, types)
    except RuleError as err:
        return str(err)
    return None


def refs(text: str) -> set[str]:
    """Type names a rule uses (empty for a type definition or a rule in all or one document)."""
    r = parse(text)
    return {r.scope[1]} if isinstance(r, Rule) and r.scope[0] == "type" and r.scope[1] else set()


def type_name(text: str) -> str:
    r = parse(text)
    if not isinstance(r, TypeDef):
        raise RuleError(1, "not a type definition", "type hkex = hkex__*")
    return r.name
