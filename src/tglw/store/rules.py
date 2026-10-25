"""Agent-written rules on disk, and the recommended rules that ship with the code.

Two layers. The recommended layer is recommended.json in this package ({"release", "entries"}), each entry
with a stable id ("rec-...") and a version. The user layer is rules_dir()/rules.json: the rules and types
added on this machine, overrides of recommended entries (switched off, or replaced by a user copy), and
all_off. Entries keep their text and language version; scope and budget are part of the text, which
tglw.trace.rules parses on every load (an entry that no longer parses is listed as invalid and not run).

The user layer also keeps the suggestions the user declined (decline()); suppressed() gives the rules not to
suggest: those, and every rule already in either layer. A suggestion the user accepts is added with
suggested=True, and its source says so.

Every change takes rules_dir()/.lock, rewrites rules.json atomically and appends to history.jsonl the whole
user layer as it was before, so undo() puts that back. reset() empties the user layer (the recommended rules
apply again); all_off() makes nothing apply (the v1 behaviour). Both are changes like any other and can be
undone. The directory is made 0700 when this module creates it (an existing one keeps its permissions);
the files are 0600. Nothing here uses the network.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from tglw.common.paths import rules_dir
from tglw.trace import rules as lang

try:
    import fcntl
except ImportError:  # no advisory locks (Windows): two processes changing rules at the same moment could clash
    fcntl = None

RECOMMENDED = Path(__file__).with_name("recommended.json")
RECOMMENDED_SWITCH = "TGLW_RULES_RECOMMENDED"  # 0: the recommended layer is left out (an evaluation that needs no rules)
LANG_VERSION = 0
FORMAT = 1
HISTORY_MAX_LINES, HISTORY_MAX_BYTES, HISTORY_KEEP = 1000, 1 << 20, 200
SUGGESTED = "suggested from your readings, accepted by the user"


class StoreError(ValueError):
    """A change the store refuses; the message says what to do instead."""


@dataclass(frozen=True)
class Entry:
    """One rule or type as it stands: on or not, from which layer, in what state, and its parse."""

    id: str
    kind: str                       # "rule" | "type"
    ver: int
    text: str
    on: bool
    layer: str                      # "recommended" | "user"
    state: str                      # as-shipped, off, replaced, not-shipped, user, user-off, invalid
    source: dict[str, Any]
    parsed: lang.Rule | lang.TypeDef | None = None
    error: str | None = None
    newer_shipped: bool = False     # a recommended entry this machine changed or switched off has a newer version


@dataclass(frozen=True)
class RuleSet:
    version: int
    all_off: bool
    release: str
    entries: tuple[Entry, ...] = field(default_factory=tuple)

    @property
    def effective(self) -> list[Entry]:
        return [] if self.all_off else [e for e in self.entries if e.on and e.parsed is not None]

    @property
    def types(self) -> dict[str, tuple[str, ...]]:
        return {e.parsed.name: e.parsed.patterns for e in self.effective if isinstance(e.parsed, lang.TypeDef)}


def _empty_user() -> dict[str, Any]:
    return {"format": FORMAT, "version": 0, "all_off": False, "entries": [], "overrides": {}, "declined": []}


def _recommended() -> dict[str, Any]:
    if os.environ.get(RECOMMENDED_SWITCH) == "0":
        return {"release": "", "entries": []}
    data = json.loads(RECOMMENDED.read_text()) if RECOMMENDED.exists() else {}
    return {"release": str(data.get("release", "")), "entries": list(data.get("entries", []))}


def _dir(directory: Path | None) -> Path:
    return directory or rules_dir()


def _read_user(directory: Path) -> dict[str, Any]:
    path = directory / "rules.json"
    if not path.exists():
        return _empty_user()
    data = json.loads(path.read_text())
    if data.get("format") != FORMAT:
        raise StoreError(f"{path} has format {data.get('format')!r}; this version reads format {FORMAT}")
    return {**_empty_user(), **data}


def _parse_all(raw: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[Entry]:
    """raw: (entry dict, extra fields for Entry). Types are parsed first, so rules can name them."""
    types = {}
    for e, extra in raw:
        if e["kind"] == "type" and extra["on"]:
            try:
                t = lang.parse(e["text"])
                if isinstance(t, lang.TypeDef):
                    types[t.name] = t.patterns
            except lang.RuleError:
                pass
    out = []
    for e, extra in raw:
        try:
            parsed, error = lang.parse(e["text"], types), None
        except lang.RuleError as err:
            parsed, error = None, str(err)
        out.append(Entry(e["id"], e["kind"], int(e.get("ver", 1)), e["text"], parsed=parsed, error=error,
                         **{**extra, "on": extra["on"] and parsed is not None,
                            "state": "invalid" if error else extra["state"]}))
    return out


def _build(user: dict[str, Any], rec: dict[str, Any]) -> RuleSet:
    raw = []
    shipped = {e["id"]: e for e in rec["entries"]}
    system = {"by": "system", "why": f"recommended in v{rec['release']}" if rec["release"] else "recommended"}
    for e in rec["entries"]:
        ov = user["overrides"].get(e["id"])
        state = "as-shipped" if ov is None else ("replaced" if ov.get("replaced_by") else "off")
        newer = ov is not None and int(e.get("ver", 1)) > int(ov.get("base", e.get("ver", 1)))
        raw.append((e, {"on": ov is None, "layer": "recommended", "state": state, "newer_shipped": newer,
                        "source": {**system, "why": e.get("why") or system["why"]}}))
    for rid, ov in user["overrides"].items() if os.environ.get(RECOMMENDED_SWITCH) != "0" else ():
        if rid not in shipped:
            raw.append(({"id": rid, "kind": ov.get("kind", "rule"), "ver": ov.get("base", 1), "text": ov.get("text", "")},
                        {"on": False, "layer": "recommended", "state": "not-shipped", "source": system}))
    for e in user["entries"]:
        on = e.get("on", True)
        raw.append((e, {"on": on, "layer": "user", "state": "user" if on else "user-off",
                        "source": e.get("source", {})}))
    return RuleSet(int(user["version"]), bool(user["all_off"]), rec["release"], tuple(_parse_all(raw)))


_cache: dict[Path, tuple[tuple[Any, ...], RuleSet]] = {}


def stamp(directory: Path | None = None) -> tuple[Any, ...]:
    """Changes whenever the user layer or the recommended layer changes; one stat each."""
    out: list[Any] = [str(RECOMMENDED), os.environ.get(RECOMMENDED_SWITCH)]
    for p in (_dir(directory) / "rules.json", RECOMMENDED):
        try:
            st = p.stat()
            out += [st.st_mtime_ns, st.st_size]
        except FileNotFoundError:
            out += [0, 0]
    return tuple(out)


def load(directory: Path | None = None) -> RuleSet:
    """Both layers, parsed; cached until stamp() changes."""
    d = _dir(directory)
    key = stamp(d)
    hit = _cache.get(d)
    if hit and hit[0] == key:
        return hit[1]
    rs = _build(_read_user(d), _recommended())
    _cache[d] = (key, rs)
    return rs


def effective(directory: Path | None = None) -> list[Entry]:
    """The rules and types in force: none when all_off, else switched-on entries that parse."""
    return load(directory).effective


@contextmanager
def _changing(directory: Path | None, op: str, by: str, why: str,
              rid: str | None = None) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Lock, hand out the user layer to change and a dict of extra history fields, then write both."""
    d = _dir(directory)
    try:
        d.mkdir(parents=True, mode=0o700)
        os.chmod(d, 0o700)
    except FileExistsError:
        pass
    lock = os.open(d / ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        user = _read_user(d)
        before = json.loads(json.dumps(user))
        note: dict[str, Any] = {"id": rid}
        yield user, note
        user["version"] = int(before["version"]) + 1
        _write_json(d / "rules.json", user)
        _append_history(d, {"n": user["version"], "at": time.time(), "op": op, "by": by, "why": why, **note,
                            "before": before})
    finally:
        os.close(lock)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_history(directory: Path, line: dict[str, Any]) -> None:
    path = directory / "history.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
    lines = path.read_text().splitlines()
    if len(lines) > HISTORY_MAX_LINES or path.stat().st_size > HISTORY_MAX_BYTES:
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines[-HISTORY_KEEP:]) + "\n")
        os.replace(tmp, path)


def _history(directory: Path) -> list[dict[str, Any]]:
    path = directory / "history.jsonl"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text().splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _types_in_force(user: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return _build(user, _recommended()).types


def _users_of(type_name: str, user: dict[str, Any]) -> list[str]:
    return [e.id for e in _build(user, _recommended()).effective
            if e.kind == "rule" and type_name in lang.refs(e.text)]


def _refuse_if_used(type_name: str, user: dict[str, Any]) -> None:
    using = _users_of(type_name, user)
    if using:
        raise StoreError(f"type {type_name} is still used by {', '.join(using)}; delete or change those rules first")


def _new_id(user: dict[str, Any]) -> str:
    taken = {e["id"] for e in user["entries"]} | set(user["overrides"]) | {e["id"] for e in _recommended()["entries"]}
    while True:
        rid = f"r-{secrets.token_hex(2)}"
        if rid not in taken:
            return rid


def _checked(text: str, user: dict[str, Any], replacing: str | None = None) -> tuple[str, str]:
    """(kind, text) of a valid entry; raises lang.RuleError or StoreError otherwise."""
    text = text.strip()
    parsed = lang.parse(text, _types_in_force(user))
    if isinstance(parsed, lang.TypeDef):
        clash = [e.id for e in _build(user, _recommended()).effective
                 if isinstance(e.parsed, lang.TypeDef) and e.parsed.name == parsed.name and e.id != replacing]
        if clash:
            raise StoreError(f"type {parsed.name} is already defined by {clash[0]}; change that one instead")
        return "type", text
    return "rule", text


def _find(user: dict[str, Any], rid: str) -> dict[str, Any] | None:
    return next((e for e in user["entries"] if e["id"] == rid), None)


def _shipped(rid: str) -> dict[str, Any] | None:
    return next((e for e in _recommended()["entries"] if e["id"] == rid), None)


def _unknown(rid: str) -> StoreError:
    return StoreError(f"no rule or type {rid}; list them to see the ids")


def add(text: str, by: str, why: str, directory: Path | None = None, suggested: bool = False) -> str:
    """A new rule or type (kind from the text); returns its id. suggested: a rule the system suggested from
    the readings and the user accepted; its source says so."""
    with _changing(directory, "add", by, why) as (user, note):
        kind, text = _checked(text, user)
        note["id"] = rid = _new_id(user)
        source = {"by": "system", "how": SUGGESTED, "accepted_by": by} if suggested else {"by": by}
        user["entries"].append({"id": rid, "kind": kind, "ver": 1, "lang": LANG_VERSION, "text": text, "on": True,
                                "source": {**source, "at": time.time(), "why": why}})
    return rid


def key(text: str) -> str:
    """The text that identifies a rule or type whatever its spacing and its why "..." note."""
    parsed = lang.parse(text)
    return (replace(parsed, why=None) if isinstance(parsed, lang.Rule) else parsed).text()


def decline(text: str, by: str, why: str, directory: Path | None = None) -> None:
    """Remember that the user does not want this suggested rule; it is not suggested again (until a reset)."""
    with _changing(directory, "decline", by, why) as (user, _):
        k = key(text)
        if k not in {d["text"] for d in user["declined"]}:
            user["declined"].append({"text": k, "by": by, "at": time.time(), "why": why})


def suppressed(directory: Path | None = None) -> set[str]:
    """Keys of rules not to suggest: those the user declined, and those already in either layer (on or off)."""
    d = _dir(directory)
    rs, user = load(d), _read_user(d)
    out = {x["text"] for x in user["declined"]}
    for e in rs.entries:
        try:
            out.add(key(e.text))
        except lang.RuleError:
            continue
    return out


def change(rid: str, text: str, by: str, why: str, directory: Path | None = None) -> str:
    """New text for an entry; returns the id that now holds it. A user entry keeps its id (ver + 1). A
    recommended one is switched off and replaced by a user copy with a new id."""
    with _changing(directory, "change", by, why, rid) as (user, _):
        entry = _find(user, rid)
        if entry is not None:
            kind, text = _checked(text, user, replacing=rid)
            if entry["kind"] == "type" and (kind != "type" or lang.type_name(text) != lang.type_name(entry["text"])):
                _refuse_if_used(lang.type_name(entry["text"]), user)
            entry.update(kind=kind, text=text, ver=int(entry.get("ver", 1)) + 1, lang=LANG_VERSION,
                         source={"by": by, "at": time.time(), "why": why})
            return rid
        shipped = _shipped(rid)
        if shipped is None:
            raise _unknown(rid)
        kind, text = _checked(text, user, replacing=rid)
        if shipped["kind"] == "type" and (kind != "type" or lang.type_name(text) != lang.type_name(shipped["text"])):
            _refuse_if_used(lang.type_name(shipped["text"]), user)
        copy = _new_id(user)
        user["overrides"][rid] = {"on": False, "replaced_by": copy, "base": int(shipped.get("ver", 1)),
                                  "kind": shipped["kind"], "text": shipped["text"],
                                  "by": by, "at": time.time(), "why": why}
        user["entries"].append({"id": copy, "kind": kind, "ver": 1, "lang": LANG_VERSION, "text": text, "on": True,
                                "source": {"by": by, "at": time.time(), "why": f"{why} (changed from {rid})"}})
        return copy


def set_on(rid: str, on: bool, by: str, why: str, directory: Path | None = None) -> None:
    with _changing(directory, "on" if on else "off", by, why, rid) as (user, _):
        entry = _find(user, rid)
        shipped = _shipped(rid)
        if entry is None and shipped is None:
            raise _unknown(rid)
        kind_text = (entry or shipped)["kind"], (entry or shipped)["text"]
        if not on and kind_text[0] == "type":
            _refuse_if_used(lang.type_name(kind_text[1]), user)
        if entry is not None:
            if on:
                lang.parse(entry["text"], _types_in_force(user))
            entry["on"] = on
        elif on:
            user["overrides"].pop(rid, None)
        else:
            user["overrides"][rid] = {"on": False, "base": int(shipped.get("ver", 1)), "kind": shipped["kind"],
                                      "text": shipped["text"], "by": by, "at": time.time(), "why": why}


def delete(rid: str, by: str, why: str, directory: Path | None = None) -> None:
    """Remove a user entry; for a recommended one this switches it off (it stays listed, and can come back)."""
    if _shipped(rid) is not None:
        set_on(rid, False, by, why, directory)
        return
    with _changing(directory, "delete", by, why, rid) as (user, _):
        entry = _find(user, rid)
        if entry is None:
            raise _unknown(rid)
        if entry["kind"] == "type":
            _refuse_if_used(lang.type_name(entry["text"]), user)
        user["entries"].remove(entry)


def reset(by: str, why: str, directory: Path | None = None) -> None:
    """Back to how this release ships: the user layer is emptied, the recommended rules apply."""
    with _changing(directory, "reset", by, why) as (user, _):
        user.update(_empty_user(), version=user["version"])


def all_off(by: str, why: str, off: bool = True, directory: Path | None = None) -> None:
    """No rule applies (off=True), as in v1; off=False lets them apply again."""
    with _changing(directory, "all-off" if off else "all-on", by, why) as (user, _):
        user["all_off"] = off


def undo(by: str, why: str = "", directory: Path | None = None) -> str:
    """Put back the user layer as it was before the latest change not yet undone; says which change."""
    with _changing(directory, "undo", by, why) as (user, note):
        lines = _history(_dir(directory))
        undone = {h.get("undoes") for h in lines if h.get("op") == "undo"}
        target = next((h for h in reversed(lines) if h.get("op") != "undo" and h.get("n") not in undone), None)
        if target is None:
            raise StoreError("nothing to undo")
        note.update(id=target.get("id"), undoes=target["n"])
        user.clear()
        user.update(target["before"])
    what = f"{target['op']} {target['id']}" if target.get("id") else target["op"]
    return f"undid change {target['n']} ({what}, by {target.get('by')})"


def history(limit: int = 20, directory: Path | None = None) -> list[dict[str, Any]]:
    """The latest changes, newest first, without the saved user layers."""
    return [{k: v for k, v in h.items() if k != "before"} for h in reversed(_history(_dir(directory)))][:limit]
