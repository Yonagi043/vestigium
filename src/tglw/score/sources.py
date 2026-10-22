"""The public documents behind the evaluation questions: where to download each PDF and the sha256 the
questions were written against.

A source list is a JSON list of {"name", "file", "url", "sha256", "bytes", "pages", ...}: `name` is the
registry name the questions use, `file` the PDF's file name. `fetch` downloads each PDF into a directory,
one at a time, and checks its size and sha256; a file already there with the right hash is kept. The PDFs
themselves are not distributed: publishers may revise a file under the same URL, so a mismatch is
reported, never silently accepted. An optional `alt_sha256` names another file checked to carry the same
text (a publisher's re-issue); it is accepted and reported as "alternative".
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from tglw import COMMAND

USER_AGENT = f"{COMMAND}-verify/0.1 (checksum verification of public reports)"
CHUNK = 1 << 16


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # match, alternative, mismatch, failed, kept
    sha256: str = ""
    bytes: int = 0
    detail: str = ""


VERIFIED = ("match", "alternative", "kept")


def load(path: Path) -> list[dict]:
    rows = json.loads(Path(path).read_text())
    for r in rows:
        missing = [k for k in ("name", "file", "url", "sha256") if not r.get(k)]
        if missing:
            raise ValueError(f"{path}: {r.get('name', '?')} lacks {missing}")
    return rows


def sha256(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def download(url: str, target: Path, timeout: float = 120) -> None:
    """Fetch url into target through a temporary file, so a broken download leaves nothing behind."""
    tmp = target.with_name(target.name + ".part")
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(CHUNK):
                out.write(chunk)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def check_one(row: dict, out: Path, get: Callable[[str, Path], None] = download) -> Check:
    target = out / row["file"]
    alt = row.get("alt_sha256") or ""
    if target.exists():
        digest, size = sha256(target)
        if digest in (row["sha256"], alt):
            return Check(row["name"], "kept", digest, size)
    try:
        get(row["url"], target)
    except Exception as e:  # noqa: BLE001 - any network or HTTP error is a failed check, reported as such
        return Check(row["name"], "failed", detail=f"{type(e).__name__}: {e}"[:300])
    digest, size = sha256(target)
    if digest == row["sha256"]:
        return Check(row["name"], "match", digest, size)
    if alt and digest == alt:
        return Check(row["name"], "alternative", digest, size, "the listed alternative file (alt_sha256)")
    return Check(row["name"], "mismatch", digest, size, f"expected {row['sha256']} ({row.get('bytes')} bytes)")


def fetch(rows: Sequence[dict], out: Path, only: Sequence[str] = (), pause: float = 1.0,
          get: Callable[[str, Path], None] = download, say: Callable[[str], None] = print) -> list[Check]:
    out.mkdir(parents=True, exist_ok=True)
    picked = [r for r in rows if not only or r["name"] in only]
    unknown = sorted(set(only) - {r["name"] for r in rows})
    if unknown:
        raise KeyError(f"not in the source list: {unknown}")
    results = []
    for i, row in enumerate(picked):
        if i and pause:
            time.sleep(pause)
        c = check_one(row, out, get)
        results.append(c)
        say(f"{c.status:8} {c.name}" + (f"  {c.detail}" if c.detail else ""))
    return results


def ingest_script(rows: Sequence[dict], pdfs: Path) -> str:
    """A shell script that adds every listed PDF to the library under the name the questions use."""
    import shlex

    lines = ["#!/usr/bin/env bash", "set -eu"]
    lines += [f"{COMMAND} ingest {shlex.quote(str(pdfs / r['file']))} --name {shlex.quote(r['name'])}"
              + (f" --cls {shlex.quote(r['cls'])}" if r.get("cls") else "") for r in rows]
    return "\n".join(lines) + "\n"
