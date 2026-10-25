"""Shared fixtures: `sample_home`, a document library (tglw.HOME_ENV) with the bundled sample registered and
its sidecars built, and `sample_env`, the environment for child processes that read it (see pkg/tglhome.py).
For the whole session, reading traces and rules go to a temporary directory, and the rule executor is off
(TGLW_RULES=0), unless the run sets these itself (private_traces_and_rules); a test of rules turns them on."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "pkg"))
from tglhome import RULES_SWITCH, build_sample_home, clean_env  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def private_traces_and_rules(tmp_path_factory: pytest.TempPathFactory):
    """Traces and rules written while testing (by this process or a child) go to a temporary directory,
    never to a real library, and rules do not run (outputs compare with v1) unless the run says otherwise."""
    from tglw.common.paths import RULES_ENV, TRACE_ENV

    root = tmp_path_factory.mktemp("traces_rules")
    saved = {name: os.environ.get(name) for name in (TRACE_ENV, RULES_ENV, RULES_SWITCH, "TIKTOKEN_CACHE_DIR")}
    os.environ.setdefault(TRACE_ENV, str(root / "traces"))
    os.environ.setdefault(RULES_ENV, str(root / "rules"))
    os.environ.setdefault(RULES_SWITCH, "0")
    # one token vocabulary for every temporary library, where tiktoken keeps it by default
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", os.path.join(tempfile.gettempdir(), "data-gym-cache"))
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(scope="session")
def sample_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_sample_home(tmp_path_factory.mktemp("tgl_home"))


@pytest.fixture(scope="session")
def sample_env(sample_home: Path) -> dict[str, str]:
    return clean_env(sample_home)
