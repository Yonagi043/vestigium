"""Reading traces recorded by the MCP server: through the real server -- tool calls on its worker threads,
over stdio and HTTP -- the signals and decisions are recorded where they arise, nothing is lost, and recording
changes no return. Uses only the bundled sample (conftest's sample_home)."""

from __future__ import annotations

import asyncio
import json
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import Implementation

from tglw import VERSION
from tglw.common.paths import TRACE_ENV
from tglw.store import traces
from tglw.trace import record

DOC = "gao-26-107790"


def _events(directory: Path) -> list[dict]:
    files = traces.list_traces(directory)
    assert len(files) == 1, [t.stem for t in files]
    return list(traces.read_events(files[0]))


def _kinds(items: list[dict]) -> list[str]:
    return [x["k"] for x in items]


def test_stdio_server_records_signals_and_decisions_where_they_arise(sample_home, sample_env, tmp_path):
    env = {**sample_env, TRACE_ENV: str(tmp_path / "traces")}
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "tglw.mcp_server"], env=env, cwd=str(sample_home))

    async def go() -> None:
        async with Client(params, client_info=Implementation(name="trace-test", version="1")) as c:
            await c.call_tool("read", {"doc": DOC, "addrs": ["p2"]})
            await c.call_tool("search", {"doc": DOC, "query": "FEMA"})
            await c.call_tool("read", {"doc": DOC, "addrs": "p5"})  # a string: the lenient parameter makes it a list
            await c.call_tool("read", {"doc": DOC, "addrs": ["t70"]})
            bad = await c.call_tool("search", {"doc": DOC, "query": "FEMA", "k": "many"})
            assert bad.is_error
            await c.call_tool("outline", {"doc": DOC})

    asyncio.run(go())
    events = _events(tmp_path / "traces")
    proc, calls, end = events[0], [e for e in events if e["ev"] == "call"], events[-1]
    assert proc["ev"] == "proc" and proc["transport"] == "stdio" and proc["pkg"] == VERSION
    assert end["ev"] == "end" and "lost" not in end
    assert [e["tool"] for e in calls] == ["read", "search", "read", "read", "search", "outline"]
    assert all(record.validate(e) == [] for e in events), [record.validate(e) for e in events]
    assert all("lost" not in e for e in calls)
    assert {e["conv"] for e in calls} == {"stdio"} and calls[0]["client"] == {"name": "trace-test", "version": "1"}
    assert [e["seq"] for e in calls] == sorted(e["seq"] for e in calls)

    page = calls[0]
    assert page["addr"] == [{"in": "p2", "kind": "page", "pages": [2]}]
    assert {"k": "continues", "page": 3, "sec": "s4"} in page["sig"]
    assert page["out"]["pages"] == [2] and page["out"]["tokens"] > 0 and page["out"]["images"] == 0

    found = calls[1]
    assert {"hits", "exact"} <= set(_kinds(found["sig"])) and "exact_note" in _kinds(found["dec"])
    hits = next(s for s in found["sig"] if s["k"] == "hits")
    assert hits["n"] == len(hits["items"]) > 0 and all(i["a"] for i in hits["items"])

    assert calls[2]["args"]["addrs"] == "p5"  # as the client sent it
    block = calls[3]
    assert block["addr"][0]["kind"] == "block"
    assert {d["src"] for d in block["dec"] if d["k"] == "grow"} & {"M6.page_context", "M6.next_page_small_reads"}
    assert all(d["tokens"] > 0 and d["by"] == "tool" for d in block["dec"] if d["k"] == "grow")

    rejected = calls[4]
    assert rejected["err"]["type"] == "ValidationError" and rejected["args"]["k"] == "many"
    assert "struct" in _kinds(calls[5]["sig"])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_server_records_each_conversation_from_its_worker_threads(sample_home, sample_env, tmp_path):
    port = _free_port()
    env = {**sample_env, TRACE_ENV: str(tmp_path / "traces")}
    p = subprocess.Popen([sys.executable, "-B", "-m", "tglw.mcp_server", "--http", "--port", str(port), "--preload", "none"],
                         env=env, cwd=str(sample_home), stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 120
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert p.poll() is None and time.time() < deadline, "server did not start"
                time.sleep(0.1)
        url = f"http://127.0.0.1:{port}/mcp"

        async def conversation() -> None:
            async with Client(url, mode="legacy") as c:
                await asyncio.gather(c.call_tool("read", {"doc": DOC, "addrs": ["p2"]}),
                                     c.call_tool("read", {"doc": DOC, "addrs": ["p6"]}),
                                     c.call_tool("search", {"doc": DOC, "query": "FEMA disaster"}))

        async def both() -> None:
            await asyncio.gather(conversation(), conversation())

        asyncio.run(both())
    finally:
        p.send_signal(signal.SIGINT)
        p.wait(timeout=30)
    calls = [e for e in _events(tmp_path / "traces") if e["ev"] == "call"]
    assert len(calls) == 6 and all(record.validate(e) == [] and "lost" not in e for e in calls)
    convs = {e["conv"] for e in calls}
    assert len(convs) == 2 and "stdio" not in convs
    assert all(sum(e["conv"] == c for e in calls) == 3 for c in convs)
    for e in calls:  # each signal reached its own call from the worker thread that ran it
        if e["tool"] == "read":  # the page asked for, and those the tool added (a search may have run alongside)
            added = [p for d in e["dec"] if d["k"] == "adjacent" for p in d["pages"]]
            assert sorted(e["out"]["pages"]) == sorted([int(e["args"]["addrs"][0][1:])] + added)
            assert "continues" in _kinds(e["sig"]) or any(d.get("dropped_continues") for d in e["dec"])
        else:
            assert "hits" in _kinds(e["sig"])


CHILD = """
import json, os, sys
from concurrent.futures import ThreadPoolExecutor
from tglw.trace import record
from tglw.mcp_server import server
events = []
record.set_sink(events.append)
calls = [("read", {"doc": DOC, "addrs": [f"p{p}"]}) for p in range(1, 11)] + \\
        [("search", {"doc": DOC, "query": q}) for q in ("FEMA", "mothers and young children", "infant formula")] + \\
        [("search", {"doc": DOC, "query": "FEMA", "mode": "literal"}), ("outline", {"doc": DOC}),
         ("context", {"doc": DOC, "addr": "s4"}), ("read", {"doc": DOC, "addrs": ["t70"]}),
         ("region", {"doc": DOC, "target": "p3"}), ("list_documents", {})]
def run():
    server.reset_session()
    out = []
    for tool, args in calls:
        r = getattr(server, tool)(**args)
        out.append([x if isinstance(x, str) else x.data.hex() for x in (r if isinstance(r, list) else [r])])
    return out
mode = sys.argv[1]
if mode == "same":
    os.environ["TGLW_TRACE"] = "0"
    off = run()
    n_off = len(events)
    os.environ["TGLW_TRACE"] = "1"
    on = run()
    print(json.dumps({"same": off == on, "off": n_off, "on": len(events), "calls": len(calls)}))
else:
    jobs = [(t, {**a}) for t, a in calls if t == "search" or (t == "read" and a["addrs"][0].startswith("p"))] * 4
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda c: getattr(server, c[0])(**c[1]), jobs))
    mixed = 0
    for e in (e for e in events if e["ev"] == "call"):
        if e["tool"] == "read":
            mixed += e["addr"][0]["in"] != e["args"]["addrs"][0] or int(e["args"]["addrs"][0][1:]) not in e["out"]["pages"]
        else:
            mixed += not any(s["k"] == "hits" for s in e["sig"])
    print(json.dumps({"events": len(events), "jobs": len(jobs), "mixed": mixed,
                      "lost": sum(bool(e.get("lost")) for e in events), "invalid": sum(bool(record.validate(e)) for e in events)}))
""".replace("DOC", repr(DOC))


def _child(mode: str, env: dict[str, str]) -> dict:
    out = subprocess.run([sys.executable, "-B", "-c", CHILD, mode], env=env, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_recording_changes_no_return_and_off_records_nothing(sample_env, tmp_path):
    got = _child("same", {**sample_env, TRACE_ENV: str(tmp_path / "traces")})
    assert got["same"] and got["off"] == 0 and got["on"] == got["calls"] + 1  # the calls and the proc event
    assert not (tmp_path / "traces").exists()  # no sink set here: nothing is written


def test_threads_do_not_mix_their_calls(sample_env, tmp_path):
    got = _child("threads", {**sample_env, TRACE_ENV: str(tmp_path / "traces")})
    assert got["events"] == got["jobs"] + 1 and got["mixed"] == 0 and got["lost"] == 0 and got["invalid"] == 0


def test_server_with_recording_off_writes_no_file(sample_home, sample_env, tmp_path):
    env = {**sample_env, TRACE_ENV: str(tmp_path / "traces"), "TGLW_TRACE": "0"}
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "tglw.mcp_server"], env=env, cwd=str(sample_home))

    async def go() -> None:
        async with Client(params) as c:
            await c.call_tool("read", {"doc": DOC, "addrs": ["p2"]})

    asyncio.run(go())
    assert not (tmp_path / "traces").exists()


def test_trace_tool_is_off_by_default(sample_env):
    code = ("import asyncio; from mcp import Client; from tglw.mcp_server import server\n"
            "async def go():\n    async with Client(server.server) as c:\n        return [t.name for t in (await c.list_tools()).tools]\n"
            "print(asyncio.run(go()))")
    out = subprocess.run([sys.executable, "-B", "-c", code], env=sample_env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "['list_documents', 'outline', 'read', 'context', 'search', 'region']", out.stderr[-2000:]


def test_trace_tool_shows_the_readings_in_the_reading_language(sample_home, sample_env, tmp_path):
    from tglw.common.text import count_tokens
    from tglw.trace import lang

    env = {**sample_env, TRACE_ENV: str(tmp_path / "traces"), "TGLW_TRACE_TOOL": "1"}
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "tglw.mcp_server"], env=env, cwd=str(sample_home))

    async def go() -> dict:
        async with Client(params) as c:
            tool = next(t for t in (await c.list_tools()).tools if t.name == "trace")
            await c.call_tool("search", {"doc": DOC, "query": "FEMA"})
            await c.call_tool("read", {"doc": DOC, "addrs": ["p2"]})
            out = {"tool": tool}
            for key, args in (("all", {}), ("ignored", {"ignored": True}), ("help", {"help": True}),
                              ("other", {"doc": "no-such-document"}), ("doc", {"doc": DOC, "last": 50})):
                r = await c.call_tool("trace", args)
                assert not r.is_error
                out[key] = r.content[0].text
            return out

    got = asyncio.run(go())
    definition = json.dumps({"name": "trace", "description": got["tool"].description, "input_schema": got["tool"].input_schema})
    assert got["tool"].description.startswith(lang.TRACE_TOOL_DESCRIPTION) and count_tokens(definition) < 200
    lines = got["all"].splitlines()
    assert lines[0].startswith(f"reading 1 · doc {DOC} · 2 steps")
    assert lines[1].startswith('1   find "FEMA"') and lines[2].startswith("2   read p2") and "continues p3" in lines[2]
    assert lines[-1] == "ignored: continues p3 (2)"
    assert got["ignored"].splitlines()[-1] == "ignored: continues p3 (2)" and "read p2" not in got["ignored"]
    assert got["help"] == lang.HELP
    assert got["other"].startswith("no readings")
    assert got["doc"] == got["all"]  # one reading so far, capped count
    calls = [e for e in _events(tmp_path / "traces") if e["ev"] == "call"]
    assert [e["tool"] for e in calls] == ["search", "read"] and all("lost" not in e for e in calls)  # trace is not a step


FAULT = """
import json, os, sys
import tglw.common.text as text_module
from tglw.trace import record
from tglw.mcp_server import server
from tglw.views import api
events = []
record.set_sink(events.append)
calls = [("search", {"doc": DOC, "query": "FEMA"}), ("read", {"doc": DOC, "addrs": ["p2"]}),
         ("read", {"doc": DOC, "addrs": ["t70"]}), ("read", {"doc": DOC, "addrs": ["s4", "s5", "s6"], "max_tokens": 900}),
         ("search", {"doc": DOC, "query": "infant formula", "mode": "literal"}), ("outline", {"doc": DOC}),
         ("context", {"doc": DOC, "addr": "s4"}), ("region", {"doc": DOC, "target": "fig0"}), ("list_documents", {}),
         ("read", {"doc": DOC, "addrs": ["zz9"]})]
def run():
    server.reset_session()
    out = []
    for tool, args in calls:
        try:
            r = getattr(server, tool)(**args)
            out.append([x if isinstance(x, str) else x.data.hex() for x in (r if isinstance(r, list) else [r])])
        except Exception as err:
            out.append(f"{type(err).__name__}: {err}")
    return out
os.environ["TGLW_TRACE"] = "0"
base = run()
def boom(*args, **kwargs):
    raise RuntimeError("injected")
fault = sys.argv[1]
if fault == "count_tokens":  # as record and figures count a return's tokens (both import it when they count)
    text_module.count_tokens = boom
elif fault == "proc_info":
    server.proc_info = boom
elif fault == "reports":  # every report raises, as a malformed one would
    for name in ("sig", "dec", "addr", "out", "doc"):
        setattr(record, name, boom)
else:
    api._trace_read = boom
os.environ["TGLW_TRACE"] = "1"
got = run()
record.end()
lost = {}
for e in events:
    for k, n in (e.get("lost") or {}).items():
        lost[k] = lost.get(k, 0) + n
print(json.dumps({"same": base == got, "differ": [c[0] for c, a, b in zip(calls, base, got) if a != b],
                  "calls": sum(e["ev"] == "call" for e in events), "expected": len(calls), "lost": lost}))
""".replace("DOC", repr(DOC))


@pytest.mark.parametrize("fault", ["count_tokens", "proc_info", "trace_read", "reports"])
def test_a_failing_recording_changes_no_return(sample_env, tmp_path, fault):
    out = subprocess.run([sys.executable, "-B", "-c", FAULT, fault], env={**sample_env, TRACE_ENV: str(tmp_path)},
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-3000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["same"], got["differ"]
    assert got["calls"] == got["expected"] and got["lost"], got  # every call still recorded, the failure counted


def test_context_lists_the_blocks_and_sections_its_reference_lines_show(sample_env):
    code = ("import json, re\nfrom tglw.trace import record\nfrom tglw.mcp_server import server\n"
            "events = []\nrecord.set_sink(events.append)\n"
            f"text = server.context(doc={DOC!r}, addr='p3')\n"
            "units = next(s['units'] for s in events[-1]['sig'] if s['k'] == 'struct' and s['part'] == 'refs_out')\n"
            "shown = [a for line in text.splitlines() if ' → ' in line\n"
            "         for a in re.findall(r'\\b(?:t|tab|fig|s)\\d+\\b', line.split(' → ', 1)[1].split(' (from ')[0])]\n"
            "print(json.dumps({'units': units, 'shown': shown}))")
    out = subprocess.run([sys.executable, "-B", "-c", code], env=sample_env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-3000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert "s4" in got["shown"] and sorted(set(got["shown"])) == sorted(got["units"])  # a later read of s4 is from this listing
