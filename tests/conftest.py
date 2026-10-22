"""Shared fixtures: `sample_home`, a document library (tglw.HOME_ENV) with the bundled sample registered and
its sidecars built, and `sample_env`, the environment for child processes that read it (see pkg/tglhome.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "pkg"))
from tglhome import build_sample_home, clean_env  # noqa: E402


@pytest.fixture(scope="session")
def sample_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_sample_home(tmp_path_factory.mktemp("tgl_home"))


@pytest.fixture(scope="session")
def sample_env(sample_home: Path) -> dict[str, str]:
    return clean_env(sample_home)
