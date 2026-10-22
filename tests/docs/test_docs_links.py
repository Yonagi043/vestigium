"""The public README and docs pages: links and anchors resolve, and every page has its Chinese pair."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark"  # the question package: only in the release tree, where it is checked too
PAGES = ("QUICKSTART", "CLIENTS", "TOOLS", "RESULTS", "METHODS", "RELATED", "FAQ")
PUBLIC = [ROOT / "README.md", ROOT / "README_ZH.md"] + [
    ROOT / "docs" / f"{page}{suffix}.md" for page in PAGES for suffix in ("", "_ZH")]
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"<[^>]+>", "", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    for heading in _HEADING.findall(_FENCE.sub("", path.read_text(encoding="utf-8"))):
        base = slug(heading)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def links(path: Path) -> list[str]:
    return _LINK.findall(_FENCE.sub("", path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", PUBLIC, ids=lambda p: str(p.relative_to(ROOT)))
def test_docs_page_exists(path: Path) -> None:
    assert path.is_file()


@pytest.mark.parametrize("path", PUBLIC, ids=lambda p: str(p.relative_to(ROOT)))
def test_docs_links_resolve(path: Path) -> None:
    bad = []
    for link in links(path):
        if re.match(r"[a-z]+:", link):
            continue
        target, _, anchor = link.partition("#")
        dest = (path.parent / target).resolve() if target else path
        if not BENCHMARK.exists() and dest.is_relative_to(BENCHMARK):
            continue
        if not dest.exists():
            bad.append(f"{link}: no such file")
        elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            bad.append(f"{link}: no such heading")
    assert not bad, bad


@pytest.mark.parametrize("page", PAGES)
def test_docs_pages_are_paired(page: str) -> None:
    en = links(ROOT / "docs" / f"{page}.md")
    zh = links(ROOT / "docs" / f"{page}_ZH.md")
    assert f"{page}_ZH.md" in en and f"{page}.md" in zh
    assert len(_HEADING.findall(_FENCE.sub("", (ROOT / "docs" / f"{page}.md").read_text(encoding="utf-8")))) == \
        len(_HEADING.findall(_FENCE.sub("", (ROOT / "docs" / f"{page}_ZH.md").read_text(encoding="utf-8"))))


def test_docs_slug() -> None:
    assert slug("Will my results match yours?") == "will-my-results-match-yours"
    assert slug("### `read`".lstrip("# ")) == "read"
    assert slug("不支持 MCP 的软件") == "不支持-mcp-的软件"
    assert slug("我的结果会和你们的一样吗？") == "我的结果会和你们的一样吗"
