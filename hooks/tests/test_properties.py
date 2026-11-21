import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from ratchet_guards import bash
from ratchet_guards.context import Context, Guard
from ratchet_guards.early import kind_of
from ratchet_guards.shell import Script
from ratchet_guards.verdict import ACTING_RULES, RULES, Decision, strictest
from tests.support import MANIFEST, PLUGIN_ROOT, ROOT, FakeRepo, make_guard, rules
from tests.test_corpus import ALLOWED, ASKED, DENIED, P, U


def _one_line(command: str) -> bool:
    return "\n" not in command and "<<" not in command


def _context_free(command: str) -> bool:
    return not any(word in command for word in ("cd ", "cd", "switch", "checkout", "symbolic-ref"))


def _sets_a_variable(command: str) -> bool:
    return any(word in command for word in ("S=", "T=", "export ", "declare "))


_SHELL_CHANGERS = frozenset(
    {"alias", "shopt", "enable", "function", "trap", "eval", ".", "source", "typeset", "{", "if"}
)


def _changes_the_shell(command: str) -> bool:
    first = command.split(maxsplit=1)[0]
    return first in _SHELL_CHANGERS or "=" in first


def _prefixes(wrap: Callable[[str], str]) -> bool:
    return wrap("a; b").endswith(" a; b")


FLAGGED = [
    (command, expected)
    for command, expected in DENIED + ASKED
    if _one_line(command) and P not in expected
]
ON_MAIN = make_guard(FakeRepo(branch="main"))
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
    asks = {
        "merge-pr-unadopted",
        "merge-default-unadopted",
        "agent-ready-label",
        "unreadable",
        *ACTING_RULES.values(),
    }
    want = Decision.ASK if set(expected) <= asks else Decision.DENY
    assert verdict.decision is want


_SEGMENT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=8)


@st.composite
def guarded_paths(draw: st.DrawFn) -> str:
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


def test_no_hypothesis_profile_fails_a_test_for_the_hosts_load() -> None:
    profiles = settings._profiles  # noqa: SLF001 # -- hypothesis names its profiles nowhere public
    late = {name: profile.deadline for name, profile in profiles.items() if profile.deadline}
    assert {"default", "ci"} <= profiles.keys()
    assert late == {}, "a wall-clock deadline fails a deterministic test when the host is busy"


_JUDGE = """
import json
import sys

sys.path.insert(0, sys.argv[1])
from ratchet_guards import bash
from ratchet_guards.context import Context
from ratchet_guards.verdict import strictest
from tests.support import ROOT, make_guard


class Spent(Exception):
    pass


work = [0, 0]
parse = bash.parse


def counted(text, *, subshells=True):
    work[0] += len(text)
    if work[1] and work[0] > work[1]:
        raise Spent
    return parse(text, subshells=subshells)


bash.parse = counted
results = []
for command, budget in json.load(sys.stdin):
    work[:] = [0, budget]
    try:
        verdicts = bash.check(command, Context(ROOT), make_guard())
    except Spent:
        results.append(None)
        continue
    except RecursionError:
        results.append([1, ["unreadable"], work[0]])
        continue
    top = strictest(verdicts)
    decision = 0 if top is None else int(top.decision)
    results.append([decision, sorted({v.rule for v in verdicts}), work[0]])
json.dump(results, sys.stdout)
"""
Judged = tuple[int, list[str], int] | None
HEAD = PLUGIN_ROOT / "hooks"


def _judge(tree: Path, cases: list[tuple[str, int]]) -> list[Judged]:
    done = subprocess.run(  # noqa: S603 # -- this interpreter on the fixed script above
        [sys.executable, "-c", _JUDGE, str(tree)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
        cwd=tree,
    )
    return [None if row is None else (row[0], row[1], row[2]) for row in json.loads(done.stdout)]


@pytest.fixture(scope="module")
def released(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tags = _run("git", "-C", str(PLUGIN_ROOT), "tag", "--list", "v*", "--sort=-version:refname")
    archive = _run(
        "git", "-C", str(PLUGIN_ROOT), "archive", tags.split()[0].decode(), "hooks", "skills"
    )
    tree = tmp_path_factory.mktemp("released")
    _run("tar", "-x", "-C", str(tree), given=archive)
    return tree / "hooks"


def _run(*argv: str, given: bytes = b"") -> bytes:
    done = subprocess.run(argv, input=given, capture_output=True, check=True)  # noqa: S603 # -- a fixed argv, no shell
    return done.stdout


def _deny_commands() -> dict[str, str]:
    denied = [rule for rule, kind in RULES.items() if kind.decision is Decision.DENY]
    found: dict[str, str] = {}
    for command, expected in DENIED:
        if not set("\n<'\"`$(){}") & set(command):
            for rule in set(expected) & set(denied) & set(rules(command)):
                found.setdefault(rule, command)
    return found


def _backticks(command: str, depth: int) -> str:
    for level in range(depth):
        ticks = "\\" * (2**level - 1) + "`"
        command = f"a {ticks}{command}{ticks}" if level else f"`{command}`"
    return "echo " + command


NESTINGS: dict[str, Callable[[str, int], str]] = {
    "substitution": lambda c, d: "echo " + "$(a " * d + f"$({c})" + ")" * d,
    "substitution over lines": lambda c, d: "echo " + "$(\n" * d + f"$({c})" + "\n)" * d,
    "backticks": lambda c, d: _backticks(c, min(d, 10)),
    "function substitution": lambda c, d: "echo " + "${ : " * d + f"${{ {c}; }}" + "; }" * d,
    "subshell": lambda c, d: "( " * d + c + " )" * d,
    "group": lambda c, d: "{ " * d + c + "; }" * d,
    "heredoc in heredoc": lambda c, d: (
        "cat <<E\n"
        + "".join(f"$(cat <<F{i}\n" for i in range(d))
        + f"$({c})"
        + "".join(f"\nF{i}\n)" for i in reversed(range(d)))
        + "\nE"
    ),
    "mixed": lambda c, d: "echo " + _mixed(f"$({c})", d),
}


def _mixed(command: str, depth: int) -> str:
    wraps = ["$(a {s})", "$( {s} )", "${{ : {s}; }}", "$({{ {s}; }})", "$(cat <<F{i}\n{s}\nF{i}\n)"]
    for level in range(depth):
        command = wraps[level % len(wraps)].format(s=command, i=level)
    return command


DEPTHS = [*range(1, 13), 20, 40, 64, 65, 300, 1000]
PROSE = "This line of the note explains a step, with `code`, $5 and a path like src/app/main.py.\n"


def _filled(size: int) -> str:
    return (PROSE * (size // len(PROSE) + 1))[:size]


ORDINARY = [
    "git commit -m \"$(cat <<'EOF'\n" + _filled(60_000) + '\nEOF\n)"',
    "gh pr create --title t --body \"$(cat <<'EOF'\n" + _filled(60_000) + '\nEOF\n)"',
    "for f in "
    + " ".join(f"src/module_{i:04d}/file_{i:04d}.py" for i in range(3000))
    + '; do wc -l "$f"; done',
    "cat > notes.md <<'EOF'\n" + _filled(140_000) + "\nEOF",
    "cat > notes.md <<'EOF'\n" + _filled(1_000_000) + "\nEOF",
]
SPENT = 10_000
_UNQUOTED_DELIMITER = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(\w+)")


def _compared() -> list[str]:
    commands = [c for c, _ in DENIED + ASKED] + ALLOWED + ORDINARY
    for rule, command in sorted(_deny_commands().items()):
        depths = DEPTHS if rule == "force-push" else [1, 9]
        commands += [nest(command, d) for nest in NESTINGS.values() for d in depths]
    return commands


def test_the_guard_is_never_looser_than_the_released_guard_and_stricter_only_in_a_body(
    released: Path,
) -> None:
    commands = _compared()
    before = _judge(released, [(c, SPENT + 4 * len(c)) for c in commands])
    after = _judge(HEAD, [(command, 0) for command in commands])
    looser, newly = [], []
    for command, old, new in zip(commands, before, after, strict=True):
        assert new is not None
        if old is None:
            looser += [(command[:120], "released past its budget", new)] if new[0] < 2 else []
        elif new[0] < old[0] or set(old[1]) - set(new[1]):
            looser.append((command[:120], old, new))
        elif old[0] == 0 < new[0]:
            newly.append(_UNQUOTED_DELIMITER.sub(r"<<\1'\2'", command))
    quoted = _judge(HEAD, [(command, 0) for command in newly])
    asked = [command[:120] for command, now in zip(newly, quoted, strict=True) if now and now[0]]
    assert (looser, asked) == ([], [])


LINES = [
    "echo " + "$(\n" * 18,
    "echo " + "$(a " * 12 + "x" + ")" * 12,
    "echo " + "`" * 4_000,
    "echo " + "$() " * 5_000,
    "echo " + " ".join(f"$(a{i})" for i in range(500)),
    "echo " + " ".join(f"`a{i}`" for i in range(500)),
    "; ".join(f"cd d{i}" for i in range(60)),
    "; ".join(f"a{i}=1" for i in range(500)),
    " | ".join("python3 -" for _ in range(100)),
    " && ".join(f"echo {i}" for i in range(500)),
    "case x in " + " ".join(f"a{i}) :;;" for i in range(500)) + " esac",
    "echo " + " ".join("${ :; }" for _ in range(500)),
    " ".join(["eval"] * 50) + " x",
    "".join("cat <<E\nb\nE\n" for _ in range(500)),
    *ORDINARY,
]
SMALL = 200


def test_the_guard_does_no_more_work_than_the_released_guard_on_a_command_line(
    released: Path,
) -> None:
    after = _judge(HEAD, [(command, 0) for command in LINES])
    spent = [0 if row is None else row[2] for row in after]
    before = _judge(released, [(c, work + SMALL) for c, work in zip(LINES, spent, strict=True)])
    costlier = [
        (command[:60], work, row[2])
        for command, work, row in zip(LINES, spent, before, strict=True)
        if row is not None and work > row[2] + SMALL
    ]
    assert costlier == []


def _work(command: str) -> int:
    spent = [0]
    parse = bash.parse

    def counted(text: str, *, subshells: bool = True) -> Script:
        spent[0] += len(text)
        return parse(text, subshells=subshells)

    with patch.object(bash, "parse", counted):
        rules(command)
    return spent[0]


def _body_costs_no_more_than_a_double_quoted_word(text: str) -> None:
    body = _work(f"cat <<E > out.md\n{text}\nE")
    line = _work(f'echo "{text}" > out.md')
    assert body <= line + SMALL, (body, line)


@pytest.mark.parametrize(
    "text",
    [
        "x\\\n" * 20_000,
        "price $5 and $HOME and ${name} and $$ end\n" * 2_000,
        ("`" * 80 + "\n") * 400,
        "$(\n" * 100,
        "".join(f"$(cat <<F{i}\n" for i in range(60))
        + "".join(f"\nF{i}\n)" for i in reversed(range(60))),
        "${ :; }\n" * 1_500,
        ("\\" * 31 + "x") * 4_000,
        " ".join(f"$(a{i})" for i in range(2_000)),
        _filled(100_000),
    ],
)
def test_a_long_heredoc_body_costs_no_more_than_the_same_text_in_double_quotes(text: str) -> None:
    _body_costs_no_more_than_a_double_quoted_word(text)


@given(text=st.text(alphabet=st.sampled_from(list("ab $()`'\\;&|<>\n#{}-=~*:")), max_size=80))
def test_a_heredoc_body_costs_no_more_than_the_same_text_in_double_quotes(text: str) -> None:
    _body_costs_no_more_than_a_double_quoted_word(text)
