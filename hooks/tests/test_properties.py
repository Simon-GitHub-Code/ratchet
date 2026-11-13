"""Hypothesis properties of the Bash guard: context never unblocks a denied command."""

import shlex
from collections.abc import Callable

from hypothesis import assume, given
from hypothesis import strategies as st

from ratchet_guards import bash
from ratchet_guards.context import Context, Guard
from ratchet_guards.early import kind_of
from ratchet_guards.verdict import ACTING_RULES, Decision, strictest
from tests.support import MANIFEST, ROOT, FakeRepo, make_guard, rules
from tests.test_corpus import ALLOWED, ASKED, DENIED, P, U


def _one_line(command: str) -> bool:
    return "\n" not in command and "<<" not in command


def _context_free(command: str) -> bool:
    return not any(word in command for word in ("cd ", "cd", "switch", "checkout", "symbolic-ref"))


def _sets_a_variable(command: str) -> bool:
    """Whether the command sets a variable for a later command of its own line.

    A wrapper in front (``sudo S=/x``) hands the assignment to the wrapped program, not to the
    shell, so the later command no longer meets it and the row is no longer the same command.
    """
    return any(word in command for word in ("S=", "T=", "export ", "declare "))


_SHELL_CHANGERS = frozenset(
    {"alias", "shopt", "enable", "function", "trap", "eval", ".", "source", "typeset", "{", "if"}
)


def _changes_the_shell(command: str) -> bool:
    """Whether the command's first part changes what a later part of its line means.

    A wrapper in front only of that part (``sudo alias cd=true; cd /tmp``) runs a program, not
    the builtin, the assignment or the group, so the later part meets a plain shell: another
    command.
    """
    first = command.split(maxsplit=1)[0]
    return first in _SHELL_CHANGERS or "=" in first


def _prefixes(wrap: Callable[[str], str]) -> bool:
    """Whether ``wrap`` only puts words in front, so it wraps the first part of a line alone."""
    return wrap("a; b").endswith(" a; b")


FLAGGED = [
    (command, expected)
    for command, expected in DENIED + ASKED
    if _one_line(command) and P not in expected
]
ON_MAIN = make_guard(FakeRepo(branch="main"))
# Why: a write the merge judges is refused only where it lands on main, so it is judged there.
JUDGED: list[tuple[str, list[str], Guard | None]] = [
    (command, expected, None) for command, expected in FLAGGED
] + [
    (command, [rule for rule in expected if rule != U], ON_MAIN)
    for command, expected in DENIED + ASKED
    if _one_line(command) and P in expected
]
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
    flagged=st.sampled_from(JUDGED),
    before=st.lists(st.sampled_from(HARMLESS_CONTEXT_FREE), max_size=4),
    after=st.lists(st.sampled_from(HARMLESS), max_size=4),
    separator=SEPARATORS,
)
def test_interleaving_harmless_commands_never_unblocks_a_flagged_one(
    *,
    flagged: tuple[str, list[str], Guard | None],
    before: list[str],
    after: list[str],
    separator: str,
) -> None:
    command, expected, guard = flagged
    line = separator.join([*before, command, *after])
    assert set(expected) <= set(rules(line, guard))


@given(
    flagged=st.sampled_from([row for row in JUDGED if not _sets_a_variable(row[0])]),
    wrappers=st.lists(st.sampled_from(WRAPPERS), min_size=1, max_size=3),
)
def test_wrapping_never_unblocks_a_flagged_command(
    flagged: tuple[str, list[str], Guard | None], wrappers: list[Callable[[str], str]]
) -> None:
    command, expected, guard = flagged
    assume(not (_changes_the_shell(command) and any(_prefixes(wrap) for wrap in wrappers)))
    for wrap in wrappers:
        command = wrap(command)
    assert set(expected) <= set(rules(command, guard))


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


@given(flagged=st.sampled_from(JUDGED))
def test_the_strictest_verdict_matches_the_rules(
    flagged: tuple[str, list[str], Guard | None],
) -> None:
    command, expected, guard = flagged
    verdict = strictest(bash.check(command, Context(ROOT), guard or make_guard()))
    assert verdict is not None
    asks = (["merge-pr"], ["agent-ready-label"], ["unreadable"])
    want = Decision.ASK if expected in asks else Decision.DENY
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
def test_every_shell_write_to_a_guard_path_is_refused_where_it_takes_effect(
    path: str, write: Callable[[str], str]
) -> None:
    group = MANIFEST.group_of(path)
    assert group is not None
    locked = ["guard-path-write"] if group.locked else []
    kind = kind_of(path)
    asked = [ACTING_RULES[kind]] if kind is not None else []
    assert rules(write(path)) == sorted(locked + asked)
    assert rules(write(path), ON_MAIN) == sorted((locked or ["guard-path-default"]) + asked)


@given(name=_SEGMENT, write=st.sampled_from(WRITES))
def test_shell_writes_to_product_code_are_allowed(name: str, write: Callable[[str], str]) -> None:
    path = f"app/adapters/{name}.py"
    assert MANIFEST.group_of(path) is None
    assert rules(write(path)) == []


# Constructs the guard reads as bash does: a line made only of these has its subshells followed.
MODELLED = [
    "ls",
    "true",
    "x=1",
    "echo ')' '('",
    'echo "a ) b"',
    "echo \\) \\(",
    "echo $(pwd) `pwd`",
    'echo "$(ls)"',
    "echo ${v:-)} ${v#(}",
    'echo "${v:-)}"',
    "echo $'a\\\\'",
    "echo `echo \\`echo )\\``",
    "echo $[ ( 1 ) ]",
    "echo $$",
    "a=(1 2)",
    "ls | wc -l",
    "ls 2>&1 >/dev/null",
    "{ ls; }",
    "if true; then ls; fi",
    "while false; do :; done",
    "git status",
    "(ls)",
    "(cd /tmp && ls)",
]
# Constructs it does not model: a heredoc, a case, a quote left open or one bash and sh end apart,
# a `${ cmd; }`, and the commands that run text the line does not show or change what a later
# word means.
UNMODELLED = [
    "echo $'\\')'",
    "echo \"${x:-'}'}\"",
    "cat <<E\n)\nE\n",
    "true |<<E cat\n)\nE\n",
    "cat <<'E F'\n)\nE F\n",
    "case a in a) :;; esac",
    "x=${ :; }",
    'echo "${| :; }"',
    'echo "$(echo case)"',
    "eval :",
    "command eval :",
    "f() { :; }",
    "function f { :; }",
    "alias a=b",
    "source /dev/null",
    ". /dev/null",
    "trap : EXIT",
    "shopt -s extglob",
    "enable -n test",
    "cat <(cd /tmp) > out.txt",
    "(( x = 1 << y ))\ny\n",
]
SUBSHELL_MOVES = [
    "(cd .github/workflows)",
    "(cd .github/workflows && ls)",
    "( (cd .github/workflows) )",
    "(cd .github/workflows) | cat",
    "(cd .github/workflows; ls) > out.txt",
]
_LATER = st.sampled_from(["; ", "\n", " && ", " || "])
_PLACES = st.sampled_from(["before", "inside", "after"])


def _line(parts: list[str], later: str) -> str:
    """``parts`` as one command line, each part ended so the next starts a command."""
    return "".join(part if part.endswith("\n") else part + later for part in parts)


@given(
    move=st.sampled_from(SUBSHELL_MOVES),
    construct=st.sampled_from(UNMODELLED),
    place=_PLACES,
    harmless=st.lists(st.sampled_from(MODELLED), max_size=3),
    later=_LATER,
)
def test_a_subshell_cd_stays_in_force_on_a_line_the_guard_does_not_model(
    *, move: str, construct: str, place: str, harmless: list[str], later: str
) -> None:
    assume(not (place == "inside" and "\n" in construct))
    if place == "inside":
        move = move.replace("(cd .github/workflows", f"(cd .github/workflows; {construct.strip()}")
    parts = {"before": [construct, move], "inside": [move], "after": [move, construct]}[place]
    line = _line([*harmless, *parts], later) + "echo x > ci.yml"
    assert {"workflow-write", "unreadable"} & set(rules(line))


@given(
    move=st.sampled_from(SUBSHELL_MOVES),
    harmless=st.lists(st.sampled_from(MODELLED), max_size=4),
    after=st.lists(st.sampled_from(MODELLED), max_size=2),
    later=_LATER,
)
def test_a_subshell_cd_is_left_behind_on_a_line_the_guard_models(
    *, move: str, harmless: list[str], after: list[str], later: str
) -> None:
    line = _line([*harmless, move, *after], later) + "echo x > ci.yml"
    assert rules(line) == []


@given(text=st.text(max_size=200))
def test_any_text_is_judged_without_raising(text: str) -> None:
    rules(text)


@given(text=st.text(alphabet=st.sampled_from(list("ab $()`'\"\\;&|<>\n#{}-=~*")), max_size=60))
def test_shell_shaped_noise_is_judged_without_raising(text: str) -> None:
    rules(text)
