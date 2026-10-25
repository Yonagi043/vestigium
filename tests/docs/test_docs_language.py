"""The reading-language pages quote the product rather than restate it: the help text is the server's,
word for word; every rule example parses; every quoted error is what the parser returns."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tglw.trace import lang, rules

DOCS = Path(__file__).resolve().parents[2] / "docs"
_HELP = re.compile(r"<!-- help:start -->\n```text\n(.*?)\n```\n<!-- help:end -->", re.S)
_BLOCK = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)


def blocks(name: str, kind: str) -> list[str]:
    return [body for k, body in _BLOCK.findall((DOCS / name).read_text(encoding="utf-8")) if k == kind]


@pytest.mark.parametrize("name", ["LANGUAGE.md", "LANGUAGE_ZH.md"])
def test_docs_help_is_verbatim(name: str) -> None:
    found = _HELP.findall((DOCS / name).read_text(encoding="utf-8"))
    assert found == [lang.HELP.rstrip("\n")]


@pytest.mark.parametrize("name", ["RULES.md", "RULES_ZH.md"])
def test_docs_rule_examples_parse(name: str) -> None:
    lines = [line for body in blocks(name, "rules") for line in body.splitlines() if line.strip()]
    assert lines
    for line in lines:
        rules.parse(line)


@pytest.mark.parametrize("name", ["RULES.md", "RULES_ZH.md"])
def test_docs_rule_errors_are_real(name: str) -> None:
    quoted = 0
    for body in blocks(name, "text"):
        lines = body.splitlines()
        for wrong, said in zip(lines[::2], lines[1::2]):
            with pytest.raises(rules.RuleError) as err:
                rules.parse(wrong)
            assert str(err.value) == said.strip()
            quoted += 1
    assert quoted


@pytest.mark.parametrize("name", ["RULES.md", "RULES_ZH.md"])
def test_docs_quick_reference_is_verbatim(name: str) -> None:
    assert lang.RULES_QUICK.rstrip("\n") in (DOCS / name).read_text(encoding="utf-8")
