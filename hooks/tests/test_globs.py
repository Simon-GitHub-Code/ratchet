"""Glob matching agrees with ``PurePosixPath.full_match`` and the gates' copy agrees with it."""

import sys
from pathlib import PurePosixPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import gatelib
from ratchet_guards import globs

CASES = [
    ("src/core/a.py", "src/core/**", True),
    ("src/core", "src/core/**", False),
    ("src/core/", "src/core/**", False),
    ("src/core/x", "src/core/**", True),
    ("src/core/a/b/c.py", "src/**/*.py", True),
    ("src/a.py", "src/**/*.py", True),
    ("a.py", "**/*.py", True),
    ("x/y/a.py", "**/*.py", True),
    ("src/core/a.py", "src/*.py", False),
    ("src/core/a.py", "src/*/*.py", True),
    ("src/core/a.py", "src/?ore/a.p?", True),
    ("src/core/a.py", "src/[cd]ore/*.py", True),
    ("src/core/a.py", "src/[!c]ore/*.py", False),
    ("a/b", "a//b", True),
    ("./a/b", "a/b/", True),
    ("docs/adr/0001-x.md", "docs/adr/[0-9][0-9][0-9][0-9]-*.md", True),
    ("anything", "**", True),
    ("", "**", True),
    ("", "**/**", True),
    ("", "", True),
    ("", "*", False),
    ("", "**/*", False),
    (".", "**", True),
    ("a", "", False),
    (".claude/worktrees/x/a.py", ".claude/**", True),
    (".claude/worktrees/x/a.py", ".claude/worktrees/**", True),
    ("a[1].py", "a[[]1].py", True),
    ("a[.py", "a[.py", True),
    ("a^b", "a[^]b", True),
    ("/home/u/.claude/plugins/x", "/home/u/.claude/plugins/**", True),
    ("src/core/a.py", "src/core/**/", True),
    ("/", "/*", False),
    ("/", "/**", True),
    ("/", "/***", True),
    ("/a", "/*", True),
    ("//", "//*", False),
    ("", "***", True),
    ("", "?", False),
]


@pytest.mark.parametrize(("path", "pattern", "expected"), CASES)
def test_matches_known_cases(path: str, pattern: str, *, expected: bool) -> None:
    assert globs.matches(path, pattern) is expected
    assert gatelib.matches(path, [pattern]) is expected


SEGMENT = st.text(alphabet="ab./*?[]!-", min_size=0, max_size=6)


@settings(max_examples=400)
@given(path=SEGMENT, pattern=SEGMENT)
def test_the_two_copies_agree(path: str, pattern: str) -> None:
    assert globs.matches(path, pattern) == gatelib.matches(path, [pattern])


@pytest.mark.skipif(sys.version_info < (3, 13), reason="full_match is the 3.13 reference")
@settings(max_examples=400)
@given(path=SEGMENT, pattern=SEGMENT)
def test_matches_agrees_with_full_match(path: str, pattern: str) -> None:
    expected = PurePosixPath(path).full_match(pattern)  # type: ignore[attr-defined,unused-ignore]
    assert globs.matches(path, pattern) == expected, (path, pattern)


def test_an_unclosed_class_is_a_literal_bracket_and_the_regex_is_cached() -> None:
    assert globs.matches("a[b", "a[b")
    assert not globs.matches("ab", "a[b")
    assert globs.regex("x/**/y") is globs.regex("x/**/y")
    assert globs.regex("x/**/y").pattern == "x/(?:[^/]+/)*y"
    assert globs.regex("**").pattern == ".*"
    assert globs.regex("a/**").pattern == "a/.*"
    assert globs.regex("**/a").pattern == "(?:[^/]+/)*a"
