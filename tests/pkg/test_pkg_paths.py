import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from tglw import COMMAND, HOME_ENV

CODE = ("import json, tglw.common.paths as p; print(json.dumps({k: str(getattr(p, k)) for k in "
        "('HOME', 'RESEARCH', 'DOC_DIR', 'CACHE_DIR', 'WORK', 'DATA', 'COMMON_DATA')}))")
RESEARCH_CHECKOUT = importlib.util.find_spec("tglw.common._research_defaults") is not None


def locations(**env: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("TGL_", "VESTIGIUM_"))}
    out = subprocess.run([sys.executable, "-c", CODE], env={**base, **env}, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_home_layout(tmp_path):
    got = locations(**{HOME_ENV: str(tmp_path)})
    assert got == {"HOME": str(tmp_path), "RESEARCH": "False", "DOC_DIR": str(tmp_path / "docs"),
                   "CACHE_DIR": str(tmp_path / "cache" / "slottree-docling-bundle-v1"), "WORK": str(tmp_path),
                   "DATA": str(tmp_path / "data"), "COMMON_DATA": str(tmp_path / "data" / "common")}


def test_single_locations_override_home(tmp_path):
    got = locations(**{HOME_ENV: str(tmp_path / "h")}, TGL_DATA=str(tmp_path / "d"), TGL_CACHE=str(tmp_path / "c"),
                    TGL_DOCS=str(tmp_path / "p"))
    assert (got["DATA"], got["COMMON_DATA"], got["CACHE_DIR"], got["DOC_DIR"]) == (
        str(tmp_path / "d"), str(tmp_path / "d" / "common"), str(tmp_path / "c"), str(tmp_path / "p"))


def test_default_without_home(tmp_path):
    got = locations(HOME=str(tmp_path))
    if RESEARCH_CHECKOUT:
        assert got["RESEARCH"] == "True" and got["DATA"] == str(Path(got["WORK"]) / "data")
    else:
        assert got["RESEARCH"] == "False" and got["DATA"] == str(tmp_path / f".{COMMAND}" / "data")
        assert got["CACHE_DIR"] == str(tmp_path / f".{COMMAND}" / "cache" / "slottree-docling-bundle-v1")


def test_old_home_variable_still_works(tmp_path):
    assert locations(TGL_HOME=str(tmp_path / "old"))["HOME"] == str(tmp_path / "old")
    both = locations(**{HOME_ENV: str(tmp_path / "new")}, TGL_HOME=str(tmp_path / "old"))
    assert (both["HOME"], both["RESEARCH"]) == (str(tmp_path / "new"), "False")
