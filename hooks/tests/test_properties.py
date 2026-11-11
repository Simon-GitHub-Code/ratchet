"""Hypothesis properties of the Bash guard: context never unblocks a denied command."""

import shlex
from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st

from ratchet_guards import bash
from ratchet_guards.context import Context
from ratchet_guards.verdict import Decision, strictest
from tests.support import MANIFEST, ROOT, make_guard, rules
from tests.test_corpus import ALLOWED, ASKED, DENIED


def _one_line(command: str) -> bool:
    return "\n" not in command and "<<" not in command


def _context_free(command: str) -> bool:
    return not any(word in command for word in ("cd ", "cd", "switch", "checkout", "symbolic-ref"))


FLAGGED = [(command, expected) for command, expected in DENIED + ASKED if _one_line(command)]
HARMLESS = [command for command in ALLOWED if _one_line(command) and command]
HARMLESS_CONTEXT_FREE = [command for command in HARMLESS if _context_free(command)]
SEPARATORS = st.sampled_from([";", " ; ", " && ", " || ", " | ", "\n", " & ", " |& "])

WRAPPERS: list[Callable[[str], str]] = [
    lambda c: f"bash -c {shlex.quote(c)}",
    lambda c: f"sh -c {shlex.quote(c)}",
    lambda c: f"eval {shlex.quote(c)}",
    lambda c: f"echo $({c})",
    lambda c: f'echo "$({c})"',
    lambda c: f"( {c} )",
    lambda c: f"{{ {c}; }}",
    lambda c: f"if true; then {c}; fi",
    lambda c: f"sudo {c}",
    lambda c: f"timeout 5 {c}",
    lambda c: f"nohup {c}",
    lambda c: f"bash <<'EOF'\n{c}\nEOF",
]


@given(
    flagged=st.sampled_from(FLAGGED),
    before=st.lists(st.sampled_from(HARMLESS_CONTEXT_FREE), max_size=4),
    after=st.lists(st.sampled_from(HARMLESS), max_size=4),
    separator=SEPARATORS,
)
def test_interleaving_harmless_commands_never_unblocks_a_flagged_one(
    *, flagged: tuple[str, list[str]], before: list[str], after: list[str], separator: str
) -> None:
    command, expected = flagged
    line = separator.join([*before, command, *after])
    assert set(expected) <= set(rules(line))


@given(
    flagged=st.sampled_from(FLAGGED),
    wrappers=st.lists(st.sampled_from(WRAPPERS), min_size=1, max_size=3),
)
def test_wrapping_never_unblocks_a_flagged_command(
    flagged: tuple[str, list[str]], wrappers: list[Callable[[str], str]]
) -> None:
    command, expected = flagged
    for wrap in wrappers:
        command = wrap(command)
    assert set(expected) <= set(rules(command))


@given(commands=st.lists(st.sampled_from(HARMLESS_CONTEXT_FREE), max_size=6), separator=SEPARATORS)
def test_harmless_commands_stay_harmless_together(commands: list[str], separator: str) -> None:
    assert rules(separator.join(commands)) == []


@given(
    left=st.sampled_from([c for c, _ in FLAGGED if _context_free(c)] + HARMLESS_CONTEXT_FREE),
    right=st.sampled_from([c for c, _ in FLAGGED] + HARMLESS),
    separator=SEPARATORS,
)
def test_rules_only_accumulate(left: str, right: str, separator: str) -> None:
    combined = set(rules(f"{left}{separator}{right}"))
    assert set(rules(left)) | set(rules(right)) <= combined


@given(flagged=st.sampled_from(FLAGGED))
def test_the_strictest_verdict_matches_the_rules(flagged: tuple[str, list[str]]) -> None:
    command, expected = flagged
    verdict = strictest(bash.check(command, Context(ROOT), make_guard()))
    assert verdict is not None
    want = Decision.ASK if expected in (["merge-pr"], ["agent-ready-label"]) else Decision.DENY
    assert verdict.decision is want


_SEGMENT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=8)


@st.composite
def guarded_paths(draw: st.DrawFn) -> str:
    """A concrete repository path that some manifest pattern guards."""
    pattern = draw(st.sampled_from([p for p in MANIFEST.patterns() if not p.startswith("~/")]))
    parts: list[str] = []
    for part in pattern.split("/"):
        if part == "**":
            parts.extend(draw(st.lists(_SEGMENT, min_size=1, max_size=3)))
        else:
            parts.append(part.replace("*", draw(_SEGMENT)))
    return "/".join(parts)


WRITES: list[Callable[[str], str]] = [
    lambda p: f"echo x > {p}",
    lambda p: f"echo x >> {shlex.quote(p)}",
    lambda p: f"tee {p}",
    lambda p: f"sed -i s/a/b/ {p}",
    lambda p: f"cp /tmp/a {p}",
    lambda p: f"mv {p} /tmp/b",
    lambda p: f"rm -f {p}",
    lambda p: f"git rm {p}",
    lambda p: f"cat > {p} <<'EOF'\nx\nEOF",
]


@given(path=guarded_paths(), write=st.sampled_from(WRITES))
def test_every_shell_write_to_a_guard_path_is_denied(
    path: str, write: Callable[[str], str]
) -> None:
    assert MANIFEST.group_of(path) is not None
    assert rules(write(path)) == ["guard-path-write"]


@given(name=_SEGMENT, write=st.sampled_from(WRITES))
def test_shell_writes_to_product_code_are_allowed(name: str, write: Callable[[str], str]) -> None:
    path = f"app/adapters/{name}.py"
    assert MANIFEST.group_of(path) is None
    assert rules(write(path)) == []


@given(text=st.text(max_size=200))
def test_any_text_is_judged_without_raising(text: str) -> None:
    rules(text)


@given(text=st.text(alphabet=st.sampled_from(list("ab $()`'\"\\;&|<>\n#{}-=~*")), max_size=60))
def test_shell_shaped_noise_is_judged_without_raising(text: str) -> None:
    rules(text)
