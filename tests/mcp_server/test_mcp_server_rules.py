"""Reading rules carried out by the server (the rule executor), the rules tool and previews, on the bundled sample only
(conftest's sample_home). Rules are off in tests unless one turns them on (TGLW_RULES=1): each test runs its calls
in a child process under only the rules it names, in a fresh conversation, and compares with the calls run with no
rule (v1)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters

DOC = "gao-26-107790"

PRELUDE = """
import contextlib, json, os, sys
from tglw.trace import record
from tglw.mcp_server import server, executor
D = %r

def run(texts, calls, session=None):
    '''The calls in one conversation under only `texts` (None: the rules the environment gives): for each, its
    text, images and the rules' decisions.'''
    token = server.SESSION.set(session or server.Session())
    rules = executor.override(*executor.parse(texts)) if texts is not None else contextlib.nullcontext()
    out = []
    try:
        with record.capture() as evs, rules:
            for tool, args in calls:
                r = getattr(server, tool)(**({} if tool == "list_documents" else {"doc": D}), **args)
                items = r if isinstance(r, list) else [r]
                out.append({"text": "\\n".join(x for x in items if isinstance(x, str)),
                            "images": [x.data.hex()[:64] for x in items if not isinstance(x, str)]})
    finally:
        server.SESSION.reset(token)
    for o, e in zip(out, evs):
        o["rules"] = [d for d in e["dec"] if d["k"] == "rule"]
        o["sig"] = e["sig"]
    return out
""" % DOC


def _child(body: str, env: dict[str, str], rules: str | None = "1") -> dict:
    env = {k: v for k, v in env.items() if k != "TGLW_RULES"} | ({"TGLW_RULES": rules} if rules is not None else {})
    out = subprocess.run([sys.executable, "-B", "-c", PRELUDE + body], env=env, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


CALLS = [("read", {"addrs": ["p2"]}), ("read", {"addrs": ["s4"], "max_tokens": 600}), ("search", {"query": "FEMA"}),
         ("search", {"query": "zebra unicorn", "mode": "literal"}), ("read", {"addrs": ["t70"]}),
         ("region", {"target": "fig0"}), ("outline", {}), ("context", {"addr": "s4"}), ("list_documents", {})]


def test_with_no_rule_in_effect_every_return_is_v1s(sample_env, tmp_path):
    body = f"""
from tglw.store import rules as store
calls = {CALLS!r}
os.environ["TGLW_RULES"] = "0"
v1 = run(None, calls)
os.environ["TGLW_RULES"] = "1"
none = run([], calls)                      # nothing in effect
store.all_off("test", "v1")                # the store all off (its recommended layer included)
all_off = run(None, calls)
store.all_off("test", "on", off=False)
recommended = run(None, calls)             # the recommended layer, as shipped
print(json.dumps({{"none": none == v1, "all_off": all_off == v1, "rec": [o["rules"] for o in recommended],
                   "rec_differs": [a["text"] != b["text"] for a, b in zip(recommended, v1)], "no_rules_off": [o["rules"] for o in v1]}}))
"""
    got = _child(body, {**sample_env, "VESTIGIUM_RULES_DIR": str(tmp_path / "rules")})
    from tglw.store import rules as store
    shipped = [e["id"] for e in json.loads(store.RECOMMENDED.read_text())["entries"]]
    assert got["none"] and got["all_off"] and not any(got["no_rules_off"])
    assert got["rec"][0][0]["src"] == "rec-continues" and got["rec"][0][0]["pages"] == [3]  # read p2 ends 'continues: p3'
    assert [d["src"] for d in got["rec"][3]] == (["rec-zero"] if "rec-zero" in shipped else [])  # a literal search finds nothing
    assert {d["src"] for r in got["rec"] for d in r} <= set(shipped)  # only the layer as shipped acts
    assert got["rec_differs"] == [bool(r) for r in got["rec"]]  # only the calls a rule acted on changed


def test_page_rules_add_whole_pages_and_drop_the_pointer_they_follow(sample_env):
    body = """
out = {
 "next": run(["on continues in all do add next-page"], [("read", {"addrs": ["p2"]})]),
 "widen": run(["on read where pages<=1 in all do widen 1"], [("read", {"addrs": ["p2"]})]),
 "prev": run(["on read in all do add prev-page"], [("read", {"addrs": ["p3"]})]),
 "cut": run(["on read in all do add next-page"], [("read", {"addrs": ["s4"], "max_tokens": 600})]),
 "seen": run(["on continues in all do add next-page"], [("read", {"addrs": ["p3"]}), ("read", {"addrs": ["p2"]})]),
 "v1": run([], [("read", {"addrs": ["p2"]}), ("read", {"addrs": ["p3"]})]),
}
print(json.dumps(out))
"""
    got = _child(body, sample_env)
    nxt = got["next"][0]
    assert nxt["rules"][0]["pages"] == [3] and nxt["rules"][0]["dropped"] == {"k": "continues", "page": 3}
    v1_p2, v1_p3 = got["v1"][0]["text"], got["v1"][1]["text"]
    assert nxt["text"] == v1_p2.replace("\ncontinues: p3 (s4)", "") + "\n[rule new1: add next-page]\n" + v1_p3  # a whole page, as read(p3)
    assert not any(s["k"] == "continues" and s["page"] == 4 and "via" not in s for s in nxt["sig"])  # p3's own: the rule's
    assert any(s["k"] == "continues" and s["page"] == 4 and s.get("via") == "new1" for s in nxt["sig"])
    assert got["widen"][0]["rules"][0]["pages"] == [3, 1]  # nearest first, the page after before the page before
    assert got["prev"][0]["rules"][0]["pages"] == [2]
    assert got["cut"][0]["rules"] == []  # a cut read gets no page: its rest comes first
    assert got["seen"][1]["rules"] == []  # p3 was read before in the conversation: not added again


def test_first_line_rest_and_unread_headings(sample_env):
    body = """
out = {
 "first": run(["on continues in all do add first-line"], [("read", {"addrs": ["p2"]})]),
 "rest": run(["on cut in all do add rest"], [("read", {"addrs": ["s4"], "max_tokens": 600})]),
 "small": run(["on cut in all do add rest budget 300t"], [("read", {"addrs": ["s4"], "max_tokens": 600})]),
 "heads": run(["on cut in all do add unread-headings"], [("read", {"addrs": ["s3", "s4", "s5", "s6"], "max_tokens": 700})]),
 "same": run(["on cut where same-section in all do add unread-headings"], [("read", {"addrs": ["s3", "s4", "s5", "s6"], "max_tokens": 700})]),
 "v1": run([], [("read", {"addrs": ["s4"], "max_tokens": 600}), ("read", {"addrs": ["s4"], "max_tokens": 2600})]),
}
print(json.dumps(out))
"""
    got = _child(body, sample_env)
    first = got["first"][0]
    assert first["rules"][0]["first"] == 3 and 'p3 starts: "Privacy for pumping' in first["text"]
    assert "continues: p3 (s4)" in first["text"]  # the pointer stays: only a line was added
    rest, cut_line = got["rest"][0], got["v1"][0]["text"].split("\n")[-1]
    assert rest["rules"][0]["dropped"] == {"k": "cut"} and rest["rules"][0]["pages"] and cut_line not in rest["text"]
    assert rest["text"].startswith(got["v1"][0]["text"][:-len(cut_line)] + "[rule new1: add rest]\n")
    small = got["small"][0]  # 300 tokens: the rest is read on as far as they go, and a next: ends the return again
    assert small["text"].split("\n")[-1].startswith("next: ")
    heads = got["heads"][0]
    assert heads["rules"][0]["listed"] == ["s5", "s6"] and heads["text"].split("\n")[-1].startswith("next: ")
    cut = next(x for x in got["same"][0]["sig"] if x["k"] == "next")
    assert cut["sec"] == cut["last_sec"] == "s4" and got["same"][0]["rules"]  # cut inside s4: same-section holds


def test_absence_after_searches_that_find_nothing(sample_env):
    body = """
rule = ["on zero where has-id in all do add absence"]
out = {
 "id": run(rule, [("search", {"query": '"zebra unicorn"', "mode": "literal"})]),
 "noid": run(rule, [("search", {"query": "zebra unicorn", "mode": "literal"})]),
 "found": run(rule, [("search", {"query": '"mass shelters" FEMA'})]),
 "zero": run(["on zero in all do add absence"], [("search", {"query": "zebra unicorn", "mode": "literal"}),
                                                  ("search", {"query": "zebra unicorn quasar"})]),
 "v1": run([], [("search", {"query": "zebra unicorn", "mode": "literal"})]),
}
print(json.dumps(out))
"""
    got = _child(body, sample_env)
    assert got["id"][0]["rules"][0]["act"] == "add absence"  # 0 matches and a quoted phrase: has-id holds
    assert got["noid"][0]["rules"] == [] and got["found"][0]["rules"] == []  # no identifier; something was found
    literal, bm25 = got["zero"]
    assert literal["rules"][0]["src"] == "new1" and literal["text"].startswith(got["v1"][0]["text"] + "\n[rule new1: add absence]")
    assert bm25["rules"] and any(s["k"] == "hits" and s.get("mode") == "absence" for s in bm25["sig"])


def test_images_zoom_and_what_was_seen(sample_env):
    body = """
out = {
 "image": run(["on read where figure in all do add image"], [("read", {"addrs": ["p3"]}), ("read", {"addrs": ["p3"]})]),
 "seen": run(["on read where figure in all do add image"], [("region", {"target": "fig1"}), ("read", {"addrs": ["p3"]})]),
 "zoom": run(["on view where figure in all do zoom x3"], [("region", {"target": "fig0"}), ("region", {"target": "fig0", "scale": 2.0}),
                                                         ("region", {"target": "p3"})]),
 "v1": run([], [("region", {"target": "fig0"})]),
}
print(json.dumps(out))
"""
    got = _child(body, sample_env)
    first, again = got["image"]
    assert first["rules"][0]["target"] == "fig1" and len(first["images"]) == 1 and "[rule new1: add image] fig1 · p3" in first["text"]
    assert again["rules"] == [] and not again["images"]  # seen in this conversation
    assert got["seen"][1]["rules"] == []  # the model looked at fig1 itself
    zoom, given, page = got["zoom"]
    assert zoom["rules"][0]["to"] == 3.0 and zoom["images"] != got["v1"][0]["images"] and zoom["text"].endswith("[rule new1: zoom x3]")
    assert given["rules"] == [] and page["rules"] == []  # a scale the model gave; a whole page


def test_which_rules_act_order_max_and_budgets(sample_env):
    body = """
from tglw.mcp_server.executor import Active, override
from tglw.trace import rules as lang
def act(rid, layer, text, types=None):
    return Active(rid, 1, layer, lang.parse(text, types or {}))
types = {"gao": ("gao-*",)}
mixed = [act("rec-a", "recommended", "on continues in all do add first-line"),
         act("r-b", "user", "on continues in type gao do add next-page", types),
         act("r-c", "user", "on continues in all do add first-line")]
token = server.SESSION.set(server.Session())
with override(mixed, types), record.capture() as evs:
    server.read(doc=D, addrs=["p2"])
server.SESSION.reset(token)
rec_only = [act("rec-a", "recommended", "on continues in all do add first-line"), act("r-c", "user", "on continues in all do add first-line max 1")]
out = {
 "mixed": [{k: d.get(k) for k in ("src", "over", "pages")} for d in evs[-1]["dec"] if d["k"] == "rule"],
 "max": run(["on continues in all do add next-page max 1"], [("read", {"addrs": ["p2"]}), ("read", {"addrs": ["p5"]})]),
 "budget": run(["on continues in all do add next-page budget 100t"], [("read", {"addrs": ["p2"]})]),
 "cap": run(["on read in all do widen 1 budget 3000t", "on read in all do widen 3 budget 3000t"], [("read", {"addrs": ["p6"]})]),
 "chain": run(["on continues in all do add next-page"], [("read", {"addrs": ["p2"]})]),
}
print(json.dumps(out))
"""
    got = _child(body, sample_env)
    assert got["mixed"] == [{"src": "r-b", "over": ["rec-a", "r-c"], "pages": [3]}]  # doc > type > all; user before recommended
    one, two = got["max"]
    assert one["rules"] and two["rules"] == []  # max 1 per conversation
    held = got["budget"][0]["rules"][0]
    assert held["tokens"] == 0 and held["skipped"] == [{"what": "p3", "why": "budget"}]
    added = sum(d["tokens"] for d in got["cap"][0]["rules"])
    assert 0 < added <= 4000 and any(x["why"] == "call" for d in got["cap"][0]["rules"] for x in d.get("skipped", []))
    assert got["chain"][0]["rules"][0]["pages"] == [3]  # p3's own continues p4 does not start the rule again


def test_a_failing_action_adds_nothing(sample_env):
    body = """
from tglw.views import api
from tglw.figures import actions
import tglw.search.absence as absence_module
def boom(*a, **k):
    raise RuntimeError("injected")
calls = [("read", {"addrs": ["p2"]}), ("read", {"addrs": ["s4"], "max_tokens": 600}), ("read", {"addrs": ["p3"]}),
         ("search", {"query": "zebra unicorn", "mode": "literal"})]
texts = ["on continues in all do add next-page, add first-line", "on cut in all do add rest", "on read where figure in all do add image",
         "on zero in all do add absence"]
v1 = run([], calls)
executor._page = api.first_line = actions.add_image = absence_module.absence = boom
broken = run(texts, calls)
print(json.dumps({"same": [a["text"] == b["text"] and a["images"] == b["images"] for a, b in zip(v1, broken)],
                  "errors": [[d.get("err", {}).get("msg") for d in o["rules"]] for o in broken]}))
"""
    got = _child(body, sample_env)
    assert all(got["same"][:1] + got["same"][2:])  # each action that failed left its return as v1's
    for i in (0, 2, 3):
        assert got["errors"][i] and set(got["errors"][i]) == {"injected"}


def test_previews_replay_recorded_readings_with_and_without_the_rules(sample_env, tmp_path):
    # TGLW_RULES=0 below, as research scripts set it: a preview runs the rules it is given all the same
    body = """
from tglw.store import traces
from tglw.mcp_server import preview
record.set_sink(traces.append)
os.environ["TGLW_RULES"] = "0"
server.read(doc=D, addrs=["p2"])
server.search(doc=D, query="zebra unicorn", mode="literal")
got = preview.compare(["on continues where pages<=2 in all do add next-page max 1"])
print(json.dumps({"got": got, "text": preview.show(["on continues in all do add first-line"])}))
"""
    out = _child(body, {**sample_env, "VESTIGIUM_TRACE_DIR": str(tmp_path / "traces")})
    (reading,) = out["got"]
    read, search = reading["steps"]
    assert read["tool"] == "read" and read["on"]["rules"][0]["pages"] == [3] and read["on"]["tokens"] > read["off"]["tokens"]
    assert search["on"]["rules"] == [] and search["on"]["tokens"] == search["off"]["tokens"]  # rec-zero is not in this preview
    assert out["text"].startswith(f"preview: 1 reading\nreading 1 · doc {DOC} · 2 steps\n  1   read p2")
    assert "\n      + rule new1 add first-line p3 " in out["text"] and out["text"].split("\n")[-1].startswith("total: rules act on 1 step")


def test_recommended_rules_act_in_a_server_left_as_installed(sample_home, sample_env, tmp_path):
    env = {k: v for k, v in sample_env.items() if k != "TGLW_RULES"} | {"VESTIGIUM_TRACE_DIR": str(tmp_path / "traces"),
                                                                        "VESTIGIUM_RULES_DIR": str(tmp_path / "rules")}
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "tglw.mcp_server"], env=env, cwd=str(sample_home))

    async def go() -> list[str]:
        async with Client(params) as c:
            out = []
            for addr in ("p2", "p5"):
                r = await c.call_tool("read", {"doc": DOC, "addrs": [addr]})
                out.append(r.content[0].text)
            return out

    first, second = asyncio.run(go())
    assert "\n[rule rec-continues: add next-page]\n== p3" in first and "continues: p3 (s4)" not in first
    assert "[rule" not in second  # max 1 in this conversation
    from tglw.store import traces
    (trace,) = traces.list_traces(tmp_path / "traces")
    events = list(traces.read_events(trace))
    from tglw.store import rules as store
    shipped = [e["id"] for e in json.loads(store.RECOMMENDED.read_text())["entries"]]
    assert [r["id"] for r in events[0]["rules"]] == shipped and "rec-continues" in shipped
    rules = [d for e in events if e["ev"] == "call" for d in e["dec"] if d["k"] == "rule"]
    assert [(d["src"], d["layer"], d["pages"]) for d in rules] == [("rec-continues", "recommended", [3])]


def _rules_server(sample_home, sample_env, tmp_path, **extra: str) -> StdioServerParameters:
    env = {**sample_env, "VESTIGIUM_TRACE_DIR": str(tmp_path / "traces"), "VESTIGIUM_RULES_DIR": str(tmp_path / "rules"),
           "TGLW_RULES": "1", **extra}
    return StdioServerParameters(command=sys.executable, args=["-B", "-m", "tglw.mcp_server"], env=env, cwd=str(sample_home))


def test_rules_tool_is_off_by_default(sample_home, sample_env, tmp_path):
    async def go() -> list[str]:
        async with Client(_rules_server(sample_home, sample_env, tmp_path)) as c:
            return [t.name for t in (await c.list_tools()).tools]

    assert asyncio.run(go()) == ["list_documents", "outline", "read", "context", "search", "region"]


def test_rules_tool_writes_lists_undoes_and_previews(sample_home, sample_env, tmp_path):
    params = _rules_server(sample_home, sample_env, tmp_path, TGLW_RULES_TOOL="1", TGLW_RULES_RECOMMENDED="0")

    async def go() -> dict:
        out = {}
        async with Client(params) as c:
            out["names"] = [t.name for t in (await c.list_tools()).tools]
            await c.call_tool("read", {"doc": DOC, "addrs": ["p2"]})

            async def rules(key: str, action: str, text: str = "", why: str = "") -> None:
                r = await c.call_tool("rules", {"action": action, "text": text, "why": why})
                out[key] = (r.is_error, r.content[0].text)

            await rules("bad", "add", "on continue in all do add next-page")
            await rules("advice", "add", "on continues in all do remind the model to read on")
            await rules("add", "add", "on continues in all do add first-line", "user: show where sections go on")
            await rules("type", "add", "type gao = gao-*")
            await rules("typed", "add", "on continues in type gao do add next-page max 1")
            await rules("used", "delete", out["type"][1].split()[1].rstrip(":"))
            await rules("list", "list")
            await rules("preview", "preview", "on continues in all do add next-page")
            r2 = await c.call_tool("read", {"doc": DOC, "addrs": ["p5"]})
            out["read"] = r2.content[0].text
            await rules("off", "off", "all")
            r3 = await c.call_tool("read", {"doc": DOC, "addrs": ["p6"]})
            out["read_off"] = r3.content[0].text
            await rules("undo", "undo")
            await rules("history", "history")
            await rules("help", "help")
            await rules("unknown", "remove", "r-0001")
        return out

    got = asyncio.run(go())
    assert got["names"][-1] == "rules"
    assert got["bad"][0] and "col 4: unknown signal 'continue'" in got["bad"][1] and "-- e.g. " in got["bad"][1]
    quick = got["bad"][1].split("\n\n", 1)[1]  # the first rule written wrong in a conversation gets the quick reference
    assert all(f"{sig}" in quick for sig in ("read", "continues", "cut", "zero", "saturated", "view"))
    assert got["advice"][0] and "rules act" in got["advice"][1] and quick not in got["advice"][1]  # once only
    assert not got["add"][0] and got["add"][1].startswith("added r-")
    rid, typed = got["add"][1].split()[1].rstrip(":"), got["typed"][1].split()[1].rstrip(":")
    assert got["used"][0] and typed in got["used"][1]  # a type still used is not deleted; the error names the rule
    assert "3 in effect" in got["list"][1] and rid in got["list"][1]
    assert got["preview"][1].startswith("preview: 1 reading\nreading 1 · doc " + DOC) and "+ rule new1 add next-page +p3" in got["preview"][1]
    assert "[rule " in got["read"]  # the rules written act on the next read
    assert not got["off"][0] and "[rule " not in got["read_off"]
    assert not got["undo"][0] and any(line.split(" · ")[2].startswith("undo") for line in got["history"][1].split("\n"))
    assert "on SIGNAL" in got["help"][1] and "action=list" in got["help"][1]
    assert got["unknown"][0] and "actions: list, add" in got["unknown"][1]


def test_a_rule_store_that_cannot_be_read_leaves_the_tools_as_v1(sample_env):
    body = """
from tglw.store import rules as store
calls = [("read", {"addrs": ["p2"]}), ("search", {"query": "zebra unicorn", "mode": "literal"})]
os.environ["TGLW_RULES"] = "0"
v1 = run(None, calls)
os.environ["TGLW_RULES"] = "1"
def boom(*a, **k):
    raise RuntimeError("injected")
store.load = boom
got = run(None, calls)
print(json.dumps({"same": [a["text"] == b["text"] for a, b in zip(v1, got)], "rules": [o["rules"] for o in got]}))
"""
    got = _child(body, sample_env)
    assert all(got["same"]) and not any(got["rules"])


def test_coverage_once_searches_saturate(sample_env):
    body = """
calls = [("search", {"query": "FEMA disaster providers"}), ("search", {"query": "FEMA disaster service providers"}),
         ("search", {"query": "zebra", "mode": "literal"}), ("read", {"addrs": ["p4"]}), ("search", {"query": "disaster providers FEMA"})]
print(json.dumps({"some": run(["on saturated where searches>=4 in all do add coverage"], calls),
                  "any": run(["on saturated in all do add coverage"], calls)}))
"""
    got = _child(body, sample_env)
    acted = [i for i, o in enumerate(got["some"]) if o["rules"]]
    assert acted == [4]  # saturated at the 2nd search too, but only the 4th search counts with searches>=4
    d = got["some"][4]["rules"][0]
    assert d["zeros"] == ["zebra"] and 4 in d["pages"] and d["tokens"] > 0
    text = got["some"][4]["text"].split("[rule new1: add coverage]\n")[1]
    assert 'literal searches with 0 matches: "zebra"' in text and "not in" not in text  # facts, no verdict
    assert [i for i, o in enumerate(got["any"]) if o["rules"]] == [1, 4]


def test_coverage_says_only_what_is_new_with_the_delta_switch(sample_env):
    body = """
calls = [("search", {"query": "FEMA disaster providers"}), ("search", {"query": "FEMA disaster service providers"}),
         ("search", {"query": "zebra", "mode": "literal"}), ("read", {"addrs": ["p4"]}),
         ("search", {"query": "disaster providers FEMA"}), ("search", {"query": "providers FEMA disaster"}),
         ("search", {"query": "providers FEMA disaster"})]
rule = ["on saturated in all do add coverage"]
os.environ.pop("TGLW_COVERAGE_DELTA", None)
full = run(rule, calls)
os.environ["TGLW_COVERAGE_DELTA"] = "1"
print(json.dumps({"full": full, "delta": run(rule, calls)}))
"""
    got = _child(body, sample_env)
    full, delta = got["full"], got["delta"]
    acted = [i for i, o in enumerate(full) if o["rules"]]
    assert acted == [1, 4, 5, 6] == [i for i, o in enumerate(delta) if o["rules"]]
    assert all(d["text"] == f["text"] for i, (d, f) in enumerate(zip(delta, full)) if i not in acted[1:])  # 1st as before
    said = {i: delta[i]["text"].split("[rule new1: add coverage]\n")[1] for i in acted}
    assert said[4].startswith("4 searches, 3 pages read; since the summary at search 2: +2 searches, +3 pages read\n")
    assert 'new literal searches with 0 matches: "zebra"' in said[4] and "new pages read: p3-5" in said[4]
    assert '"service" 43' not in said[4] and "hit pages listed" not in said[4]  # shown at search 2 already
    assert '"providers FEMA disaster" 0' in said[5] and "zebra" not in said[5]
    assert said[6] == "6 searches, 3 pages read; nothing new since the summary at search 5"
    d = {i: delta[i]["rules"][0] for i in acted}
    assert [d[i].get("since") for i in acted] == [None, 2, 4, 5] and d[4]["new_pages"] == [3, 4, 5]
    assert all(d[i]["tokens"] < full[i]["rules"][0]["tokens"] for i in acted[1:])
    assert all("since" not in full[i]["rules"][0] for i in acted)


def test_suggestions_from_readings_through_the_rules_tool(sample_home, sample_env, tmp_path):
    env = {**sample_env, "VESTIGIUM_TRACE_DIR": str(tmp_path / "traces"), "VESTIGIUM_RULES_DIR": str(tmp_path / "rules"),
           "TGLW_RULES": "1", "TGLW_RULES_RECOMMENDED": "0"}

    def params(**extra: str) -> StdioServerParameters:
        return StdioServerParameters(command=sys.executable, args=["-B", "-m", "tglw.mcp_server"], env={**env, **extra},
                                     cwd=str(sample_home))

    async def reading() -> None:  # 'continues: p3' after p2, not followed then; p3 read two steps later
        async with Client(params()) as c:
            for addr in ("p2", "p6", "p3"):
                await c.call_tool("read", {"doc": DOC, "addrs": [addr]})

    async def go() -> list[tuple[bool, str]]:
        for _ in range(3):
            await reading()
        out = []
        async with Client(params(TGLW_RULES_TOOL="1")) as c:
            async def rules(action: str, text: str = "") -> str:
                r = await c.call_tool("rules", {"action": action, "text": text})
                out.append((r.is_error, r.content[0].text))
                return r.content[0].text
            listed = await rules("list")
            sid = listed.split("suggested from your readings (not active):\n  ")[1].split()[0]
            await rules("off", sid)
            await rules("list")
            await rules("undo")
            await rules("add", sid)
            await rules("list")
        return out

    first, off, after_off, undo, add, after_add = asyncio.run(go())
    assert "on continues in doc gao-26-107790 do add next-page max 1" in first[1]
    assert "why: in 3 of 3 readings" in first[1] and "preview: acts on 3 steps" in first[1]
    assert not off[0] and "will not be suggested again" in off[1] and "suggested from" not in after_off[1]
    assert not undo[0] and not add[0] and add[1].startswith("added r-")
    assert "system suggestion, accepted by the user" in after_add[1] and "suggested from" not in after_add[1]


def test_a_failing_stage_3_or_4_part_changes_nothing(sample_env):
    body = """
from tglw.trace import learn
import tglw.search.coverage as coverage_module
from tglw.mcp_server import rules_tool
def boom(*a, **k):
    raise RuntimeError("injected")
calls = [("read", {"addrs": ["p5"]}), ("search", {"query": "FEMA disaster providers"}), ("search", {"query": "FEMA disaster service providers"})]
v1 = run([], calls)
coverage_module.coverage = learn.suggest = boom
broken = run(["on saturated in all do add coverage"], calls)
print(json.dumps({"same": [a["text"] == b["text"] for a, b in zip(v1, broken)],
                  "errors": [[d.get("err", {}).get("msg") for d in o["rules"]] for o in broken],
                  "suggested": rules_tool.suggested()}))
"""
    got = _child(body, sample_env)
    assert all(got["same"]) and got["errors"][0] == [] and got["errors"][2] == ["injected"]
    assert got["suggested"] == ""  # a list without suggestions is still the list
