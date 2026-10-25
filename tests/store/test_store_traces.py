import json
import os
import stat
import threading
import time

from tglw.store import traces


def proc(pid="8f3a0c1d2e4b5a6f", run=None):
    return {"v": 0, "ev": "proc", "proc": pid, "t": 1.0, "run": run}


def call(seq, **extra):
    return {"v": 0, "ev": "call", "proc": "8f3a0c1d2e4b5a6f", "seq": seq, "tool": "read", **extra}


def test_one_private_file_per_process(tmp_path):
    w = traces.Writer(tmp_path / "traces")
    w.append(proc(run="r7"))
    w.append(call(1))
    w.append(call(2))
    path = w.path
    assert path.parent == tmp_path / "traces" and path.name.endswith("-8f3a0c1d2e4b.jsonl")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [json.loads(line)["ev"] for line in path.read_text().splitlines()] == ["proc", "call", "call"]
    [trace] = traces.list_traces(tmp_path / "traces")
    assert (trace.run, trace.proc["proc"]) == ("r7", "8f3a0c1d2e4b5a6f")
    assert [e.get("seq") for e in traces.read_events(trace)] == [None, 1, 2]


def test_parts_each_start_with_the_proc_event(tmp_path):
    w = traces.Writer(tmp_path, max_file_bytes=300)
    w.append(proc())
    for i in range(10):
        w.append(call(i, pad="x" * 60))
    [trace] = traces.list_traces(tmp_path)
    assert len(trace.files) > 2
    assert all(json.loads(f.read_text().splitlines()[0])["ev"] == "proc" for f in trace.files)
    assert [e.get("seq") for e in traces.read_events(trace.stem, tmp_path)] == [None, *range(10)]


def test_threads_never_interleave(tmp_path):
    w = traces.Writer(tmp_path)
    w.append(proc())
    workers = [threading.Thread(target=lambda k=k: [w.append(call(k * 1000 + i, pad="y" * 500)) for i in range(200)])
               for k in range(8)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    seqs = [e["seq"] for e in traces.read_events(traces.list_traces(tmp_path)[0]) if e["ev"] == "call"]
    assert sorted(seqs) == sorted(k * 1000 + i for k in range(8) for i in range(200))
    assert len(w.path.read_text().splitlines()) == 1601  # every line parsed: none cut or merged


def test_oldest_deleted_first_when_full(tmp_path):
    old = []
    for i in range(3):
        p = tmp_path / f"2026010{i}T000000Z-old{i}.jsonl"
        p.write_bytes(b"x" * 1000)
        old.append(p)
    w = traces.Writer(tmp_path, max_bytes=2500)
    w.append(proc())  # opening this process's file prunes; the files above are fresh, so all stay
    assert all(p.exists() for p in old)
    stale = time.time() - 3600
    for p in old:
        os.utime(p, (stale, stale))
    removed = traces.prune(2500, tmp_path, keep=(w.path,))
    assert removed == [old[0]] and old[1].exists() and w.path.exists()


def test_failure_is_swallowed_and_reported_once(tmp_path, capsys):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    w = traces.Writer(blocker / "traces")
    w.append(proc())
    w.append(call(1))
    err = capsys.readouterr()
    assert err.out == "" and err.err.count("a reading trace was not written") == 1


def test_follows_the_trace_directory(tmp_path, monkeypatch):
    from tglw.common.paths import TRACE_ENV

    w = traces.Writer()
    monkeypatch.setenv(TRACE_ENV, str(tmp_path / "a"))
    w.append(proc())
    monkeypatch.setenv(TRACE_ENV, str(tmp_path / "b"))
    w.append(call(1))
    assert len(traces.list_traces(tmp_path / "a")) == 1
    [b] = traces.list_traces(tmp_path / "b")
    assert [e["ev"] for e in traces.read_events(b)] == ["proc", "call"]


def test_reader_skips_a_cut_line_and_clear_removes_all(tmp_path, monkeypatch):
    from tglw.common.paths import TRACE_ENV

    monkeypatch.setenv(TRACE_ENV, str(tmp_path))
    traces.append(proc())
    traces.append(call(1))
    with open(traces.current_file(), "ab") as f:
        f.write(b'{"v": 0, "ev": "call", "se')
    assert [e["ev"] for e in traces.read_events(traces.list_traces()[0])] == ["proc", "call"]
    assert traces.clear() == 1 and traces.list_traces() == []
    traces.append(call(2))
    [again] = traces.list_traces()
    assert [e["ev"] for e in traces.read_events(again)] == ["proc", "call"]


def test_an_existing_directory_keeps_its_permissions(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    w = traces.Writer(shared)
    w.append(proc())
    w.append(call(1))
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755 and stat.S_IMODE(w.path.stat().st_mode) == 0o600
