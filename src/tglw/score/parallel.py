"""Parallel headless batches: several lanes run at once, each strictly one run at a time.

A parallel stage `<dest>` (<runs>/<stage>) holds one or more sub-stages, one per thing a run id
cannot tell apart (snapshot, model, a second pass). `stage_parallel` writes, for each sub-stage,
`<dest>/<sub>/plan.csv` (its whole plan), `<dest>/<sub>/stage.json` (model and snapshot) and one
ordinary stage per lane, `<dest>/<sub>/lane<i>/`; and `<dest>/schedule.csv`: every run of every
sub-stage in one seeded order, dealt to the lanes in turn, so each condition meets the same degree of
parallelism. Records mirror it under <records> (`<records>/<sub>/lane<i>/`); `plan.ledger_rows` on
`<records>/<sub>` merges the lanes, so costs, sheets and compare read a sub-stage as one stage.

`run_parallel` gives each lane its own thread and config directory (`<config>-<i>`) and goes through
its share of the schedule, calling `headless.run_batch` for one run at a time (all its checks apply),
with the pause kept per lane. A shared stop file `<records>/STOP` stops every lane: any lane writes it
when its run stops the batch or anything else goes wrong, and each lane looks for it before starting a
run, so the others stop before their next one. Permission denials are recorded, not stopped on. Every
stop is logged in `<records>/stops.log`; a new start is refused while STOP is there, within an hour of
the last stop, and after two stops (then the user decides). Failed runs are left for a person, except
that with `retry_infra` an infrastructure failure (an API error that stopped the batch, a crash, an
error result, no session log) gets one more attempt with the same material (`plan.retry`); a run that
failed by itself (timeout, turn limit, no answer text) is never rerun.
"""

from __future__ import annotations

import csv
import json
import os
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import headless, plan, settings
from .questions import Question

SCHEDULE_COLUMNS = ("seq", "lane", "substage", "run_id", "condition", "question_id")
RUN_FAILURES = ("timeout after", "max turns reached", "no answer text")  # the run's own failures: never rerun
WAIT_AFTER_STOP = 3600
MAX_STOPS = 2


@dataclass(frozen=True)
class Substage:
    name: str
    rows: Sequence[dict[str, str]]
    questions: dict[str, Question]
    model: str
    snapshot: Path | None = None
    server_env: dict[str, str] | None = None  # T's server settings (TGLW_* switches), written into .mcp.json


def schedule(substages: Sequence[Substage], lanes: int, seed: int) -> list[dict[str, str]]:
    """Every run of every sub-stage in one seeded order, dealt to lanes 1..n in turn."""
    items = [(s.name, r) for s in substages for r in s.rows]
    random.Random(seed).shuffle(items)
    return [{"seq": str(k), "lane": str((k - 1) % lanes + 1), "substage": name, "run_id": r["run_id"],
             "condition": r["condition"], "question_id": r["question_id"]} for k, (name, r) in enumerate(items, 1)]


def stage_parallel(dest: Path, substages: Sequence[Substage], lanes: int = 3, seed: int = 0) -> list[Path]:
    """Stage every sub-stage's lanes (materials checked first) and write the schedule; refuses a
    destination that already has one."""
    names = [s.name for s in substages]
    if len(set(names)) != len(names) or any(not n or "/" in n or n.startswith("lane") for n in names):
        raise ValueError(f"sub-stage names must be distinct, plain and not lane*: {names}")
    if lanes < 1:
        raise ValueError("lanes must be at least 1")
    if (dest / "schedule.csv").exists():
        raise FileExistsError(dest / "schedule.csv")
    problems = [p for s in substages for p in plan.check_materials(s.rows)]
    if problems:
        raise FileNotFoundError("; ".join(problems))
    sched = schedule(substages, lanes, seed)
    lane_of = {(r["substage"], r["run_id"]): int(r["lane"]) for r in sched}
    made = []
    for s in substages:
        sub = dest / s.name
        sub.mkdir(parents=True, exist_ok=True)
        plan.write_plan(s.rows, sub / "plan.csv")
        (sub / "stage.json").write_text(json.dumps({"model": s.model, "snapshot": str(s.snapshot) if s.snapshot else None,
                                                    "lanes": lanes, "seed": seed, "server_env": s.server_env or {}},
                                                   indent=1) + "\n")
        for lane in range(1, lanes + 1):
            rows = [r for r in s.rows if lane_of[(s.name, r["run_id"])] == lane]
            if rows:
                made += plan.stage(rows, s.questions, sub / f"lane{lane}", s.snapshot, s.server_env)
    _write_csv(dest / "schedule.csv", SCHEDULE_COLUMNS, sched)
    return made


def substages_from_spec(spec: dict, load: Callable[..., dict[str, Question]]) -> list[Substage]:
    """Sub-stages from a JSON spec: {"substages": [{"name", "questions": [files], "only": [ids]?, "conditions",
    "model", "seed", "snapshot"?, "fulltext"? (W texts), "fulltext_v"? (V texts; default fulltext), "export"? (S folders),
    "server_env"? (T's server settings, e.g. {"TGLW_CJK": "1"})}],
    "allow_heldout"?}; `load` is questions.load_h."""
    out = []
    for s in spec["substages"]:
        qs = load([Path(p) for p in s["questions"]], bool(spec.get("allow_heldout")))
        if s.get("only"):
            missing = sorted(set(s["only"]) - set(qs))
            if missing:
                raise KeyError(f"{s['name']}: not in its question files: {missing}")
            qs = {q: qs[q] for q in s["only"]}
        rows = plan.make_plan(list(qs.values()), tuple(s["conditions"]), s["name"], int(s.get("seed", 0)),
                              fulltext_dir=Path(s["fulltext"]) if s.get("fulltext") else None,
                              fulltext_v_dir=Path(s.get("fulltext_v") or s["fulltext"]) if s.get("fulltext_v") or s.get("fulltext") else None,
                              export_dir=Path(s["export"]) if s.get("export") else None)
        out.append(Substage(s["name"], rows, qs, s["model"], Path(s["snapshot"]) if s.get("snapshot") else None,
                            {str(k): str(v) for k, v in (s.get("server_env") or {}).items()}))
    return out


def read_schedule(dest: Path) -> list[dict[str, str]]:
    with open(dest / "schedule.csv", newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def lane_configs(config: Path, lanes: Sequence[int]) -> dict[int, Path]:
    return {lane: config.with_name(f"{config.name}-{lane}") for lane in lanes}


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[dict[str, str]]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _iso(t: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t, timezone.utc).isoformat(timespec="seconds")


def halt(stop_file: Path, why: str) -> None:
    """Set the stop file; the first reason stays."""
    try:
        fd = os.open(stop_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return
    os.write(fd, f"{_iso()}\t{why}\n".encode())
    os.close(fd)


def stops(records: Path) -> list[tuple[float, str]]:
    path = records / "stops.log"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            t, _, why = line.partition("\t")
            out.append((float(t), why))
    return out


def refusal(dest: Path, records: Path, configs: dict[int, Path], now: float) -> str:
    """Why the batch may not start, or ""."""
    marker = settings.heldout_marker(dest.resolve()) or settings.heldout_marker(records.resolve())
    for lane, cfg in configs.items():
        if marker and not cfg.name.startswith(f".claude-{marker}"):
            return f"a held-out stage needs config directories named .claude-{marker}*: lane {lane} has {cfg}"
        if not marker and cfg.name.startswith(tuple(f".claude-{m}" for m in settings.HELDOUT)):
            return f"a development stage may not use a held-out config directory: lane {lane} has {cfg}"
        if not (cfg / "settings.json").is_file():
            return f"lane {lane}: {cfg}/settings.json is missing (copy it from ~/.claude before starting)"
    if (records / "STOP").exists():
        return (f"{records / 'STOP'} is set: {(records / 'STOP').read_text().strip()}; after waiting at least "
                f"{WAIT_AFTER_STOP // 3600} hour, delete it to go on")
    past = stops(records)
    if len(past) >= MAX_STOPS:
        return f"the batch has stopped {len(past)} times (see {records / 'stops.log'}); the user decides how to go on"
    if past and now - past[-1][0] < WAIT_AFTER_STOP:
        return f"the last stop was at {_iso(past[-1][0])}; wait until {_iso(past[-1][0] + WAIT_AFTER_STOP)}"
    return ""


def attempts(rec: Path, item: dict[str, str]) -> list[dict[str, str]]:
    """The item's attempts, each by its last ledger row, in order."""
    return [r for r in plan.final_rows(plan.ledger_rows(rec)).values()
            if (r["condition"], r["question_id"]) == (item["condition"], item["question_id"])]


def state(rec: Path, item: dict[str, str]) -> str:
    """new (no ledger row yet), settled (a valid or invalid attempt), infra (only failed attempts, and
    the last one an infrastructure failure) or failed (the run failed by itself)."""
    tries = attempts(rec, item)
    if not tries:
        return "new"
    if any(t["status"] in ("valid", "invalid") for t in tries):
        return "settled"
    return "failed" if any(f in tries[-1]["reason"] for f in RUN_FAILURES) else "infra"


def run_lane(dest: Path, records: Path, lane: int, items: Sequence[dict[str, str]], config_dir: Path, *,
             pause: float, timeout: float, max_turns: int, claude: str, allow_frozen: bool,
             out: Callable[[str], None], sleep: Callable[[float], None], retry_infra: bool = False) -> int:
    """One lane: its share of the schedule, one run at a time; 0 done, otherwise the batch stopped."""
    stop_file = records / "STOP"
    last = records / f"lane{lane}.last_end"
    left = []
    for item in items:
        sub, rid = item["substage"], item["run_id"]
        sub_dest, sub_rec = dest / sub / f"lane{lane}", records / sub / f"lane{lane}"
        done = state(sub_rec, item)
        if done == "infra" and retry_infra and len(attempts(sub_rec, item)) == 1:
            second = plan.run_id(item["condition"], item["question_id"], 2)
            rid = second if (sub_dest / "runs" / second).is_dir() else plan.retry(sub_dest, rid).name
            out(f"{sub}/{item['run_id']} failed for infrastructure reasons; one more attempt: {rid}")
        elif done != "new":
            if done in ("infra", "failed"):
                left.append(f"{sub}/{rid} ({'infrastructure' if done == 'infra' else 'failed by itself'})")
            continue
        if stop_file.exists():
            out(f"not starting {sub}/{rid}: the batch is stopped")
            return 2
        wait = (float(last.read_text().split()[0]) + pause - time.time()) if last.exists() else 0
        if wait > 0:
            out(f"pause {wait:.0f} s")
            sleep(wait)
        if stop_file.exists():
            out(f"not starting {sub}/{rid}: the batch is stopped")
            return 2
        model = json.loads((dest / sub / "stage.json").read_text())["model"]
        rc = headless.run_batch(sub_dest, sub_dest / "plan.csv", sub_rec, only=rid, pause=0, timeout=timeout,
                                max_turns=max_turns, claude=claude, allow_frozen=allow_frozen, config_dir=config_dir,
                                model=model, stop_on_denials=False, out=lambda s: out(f"{sub}: {s}"), sleep=sleep)
        last.write_text(f"{time.time()} {sub}/{rid}\n")
        if rc != 0:
            halt(stop_file, f"lane {lane}: {sub}/{rid} stopped the batch (runner code {rc})")
            return rc
    if left:
        out("left for a person (failed; not rerun): " + ", ".join(left))
    return 0


def run_parallel(dest: Path, records: Path, config: Path, *, pause: float = 60, timeout: float = 900,
                 max_turns: int = 60, claude: str = "claude", allow_frozen: bool = False, dry_run: bool = False,
                 retry_infra: bool = False, out: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time) -> int:
    """0: every lane done; 1: refused before starting; 2: the batch stopped (see <records>/STOP)."""
    sched = read_schedule(dest)
    lanes = sorted({int(r["lane"]) for r in sched})
    configs = lane_configs(config, lanes)
    why = refusal(dest, records, configs, now())
    if why and not dry_run:
        out(why)
        return 1
    by_lane = {lane: [r for r in sched if int(r["lane"]) == lane] for lane in lanes}
    if dry_run:
        if why:
            out(f"# would refuse now: {why}")
        for lane in lanes:
            out(f"# lane {lane}: {len(by_lane[lane])} runs, CLAUDE_CONFIG_DIR={configs[lane]}")
            for r in by_lane[lane]:
                sub = dest / r["substage"] / f"lane{lane}"
                model = json.loads((dest / r["substage"] / "stage.json").read_text())["model"]
                out(f"#  {r['seq']}  {r['substage']}/{r['run_id']}  (pause {pause:g} s before, model {model})")
                out(headless.dry_line(sub, records / r["substage"] / f"lane{lane}", r["run_id"], max_turns, timeout,
                                      claude, configs[lane], model))
        return 0
    records.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    codes: dict[int, int] = {}

    def say(lane: int) -> Callable[[str], None]:
        def emit(s: str) -> None:
            with lock:
                out(f"[L{lane}] {s}")
        return emit

    def go(lane: int) -> None:
        try:
            codes[lane] = run_lane(dest, records, lane, by_lane[lane], configs[lane], pause=pause, timeout=timeout,
                                   max_turns=max_turns, claude=claude, allow_frozen=allow_frozen, out=say(lane),
                                   sleep=sleep, retry_infra=retry_infra)
        except Exception as e:  # noqa: BLE001 - any failure in one lane stops them all
            halt(records / "STOP", f"lane {lane}: {type(e).__name__}: {e}")
            say(lane)(f"error: {type(e).__name__}: {e}")
            codes[lane] = 2

    threads = [threading.Thread(target=go, args=(lane,)) for lane in lanes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if (records / "STOP").exists():
        line = (records / "STOP").read_text().strip()
        with open(records / "stops.log", "a") as fh:
            fh.write(f"{now()}\t{line}\n")
        out(f"batch stopped: {line}")
        return 2
    return 0
