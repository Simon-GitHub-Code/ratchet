import re
import sys
import time
from collections.abc import Callable
from types import FrameType

import pytest

from ratchet_guards import bash
from ratchet_guards.context import Context
from tests.support import ROOT, make_guard

PROSE = "This line of the note explains a step, with `code`, $5 and a path like src/app/main.py.\n"
PROGRAM = "".join(
    f"def f{i}(x):\n    return [y * 2 for y in x if y % 3 == {i} % 3]\n\n" for i in range(9)
)
LINEAR = 2.3


def _filled(text: str, size: int) -> str:
    return (text * (size // len(text) + 1))[:size]


def _files(count: int) -> str:
    return " ".join(f"src/m{i}/f{i}.py" for i in range(count))


Family = tuple[int, Callable[[int], str]]
FAMILIES: dict[str, Family] = {
    "unclosed substitutions in a body": (10, lambda n: "cat <<E\n" + "$(\n" * n + "\nE"),
    "unclosed substitutions on the line": (10, lambda n: ": " + "$(\n" * n),
    "unclosed backticks": (10, lambda n: "echo " + "a `b " * n),
    "nested substitutions": (10, lambda n: "echo " + "$(a " * n + "x" + ")" * n),
    "nested substitutions over lines": (10, lambda n: "echo " + "$(\n" * n + "x" + "\n)" * n),
    "nested function substitutions": (10, lambda n: "echo " + "${ : " * n + "x" + "; }" * n),
    "nested subshells": (25, lambda n: "( " * n + "echo x" + " )" * n),
    "nested groups": (25, lambda n: "{ " * n + "echo x" + "; }" * n),
    "nested heredocs": (
        5,
        lambda n: (
            "cat <<E\n"
            + "".join(f"$(cat <<F{i}\n" for i in range(n))
            + "x"
            + "".join(f"\nF{i}\n)" for i in reversed(range(n)))
            + "\nE"
        ),
    ),
    "nested evals": (10, lambda n: " ".join(["eval"] * n) + " x"),
    "nested process substitutions": (10, lambda n: "cat " + "<(a " * n + "x" + ")" * n),
    "commands after ;": (50, lambda n: "; ".join(f"echo {i}" for i in range(n))),
    "commands after &&": (50, lambda n: " && ".join(f"echo {i}" for i in range(n))),
    "commands after ||": (50, lambda n: " || ".join(f"echo {i}" for i in range(n))),
    "commands after &": (50, lambda n: " & ".join(f"echo {i}" for i in range(n))),
    "commands on lines": (50, lambda n: "\n".join(f"echo {i}" for i in range(n))),
    "a long pipeline": (50, lambda n: " | ".join(["grep -v x", "sort", "uniq -c"] * n)),
    "words piped into a shell": (
        25,
        lambda n: "echo " + " ".join(f"w{i}" for i in range(n)) + " | bash",
    ),
    "words piped into an interpreter": (
        25,
        lambda n: "echo " + " ".join(f"w{i}" for i in range(n)) + " | python3 -",
    ),
    "a huge word": (5_000, lambda n: "echo " + "a" * n),
    "a huge single-quoted string": (5_000, lambda n: "echo '" + _filled("a b ", n) + "'"),
    "a huge double-quoted string": (5_000, lambda n: 'echo "' + _filled("a $b ", n) + '"'),
    "a huge quoted body": (
        5_000,
        lambda n: "cat > notes.md <<'EOF'\n" + _filled(PROSE, n) + "\nEOF",
    ),
    "a huge unquoted body": (
        5_000,
        lambda n: "cat > notes.md <<EOF\n" + _filled(PROSE.replace("`code`", "code"), n) + "\nEOF",
    ),
    "a huge commit message": (
        5_000,
        lambda n: """git commit -m "$(cat <<'EOF'\n""" + _filled(PROSE, n) + '\nEOF\n)"',
    ),
    "a huge program on stdin": (
        5_000,
        lambda n: "python3 - <<'EOF'\n" + _filled(PROGRAM, n) + "\nEOF",
    ),
    "a huge program argument": (
        5_000,
        lambda n: "python3 -c '" + _filled(PROGRAM.replace("'", '"'), n) + "'",
    ),
    "many heredocs": (50, lambda n: "".join("cat <<E\nb\nE\n" for _ in range(n))),
    "many heredocs with their own delimiters": (
        50,
        lambda n: "".join(f"cat <<E{i}\nb\nE{i}\n" for i in range(n)),
    ),
    "many heredocs holding a function substitution": (
        50,
        lambda n: "".join("cat <<E\n${ :; }\nE\n" for _ in range(n)),
    ),
    "many heredocs on one line": (
        50,
        lambda n: (
            "cat "
            + " ".join(f"<<E{i}" for i in range(n))
            + "\n"
            + "".join(f"b\nE{i}\n" for i in range(n))
        ),
    ),
    "many assignments": (50, lambda n: "; ".join(f"a{i}=1" for i in range(n))),
    "many assignments before a command": (
        50,
        lambda n: " ".join(f"a{i}=1" for i in range(n)) + " echo x",
    ),
    "many exported names": (50, lambda n: "export " + " ".join(f"a{i}=1" for i in range(n))),
    "many files edited in place": (25, lambda n: "sed -i 's/a/b/' " + _files(n)),
    "many files removed": (25, lambda n: "rm -f " + _files(n)),
    "many files copied": (25, lambda n: "cp " + _files(n) + " out/"),
    "many files staged": (25, lambda n: "git add " + _files(n)),
    "a loop over many files": (25, lambda n: "for f in " + _files(n) + '; do wc -l "$f"; done'),
    "a generated script": (
        25,
        lambda n: "\n".join(
            f"mkdir -p out/d{i} && cp src/f{i}.txt out/d{i}/ && echo copied {i}" for i in range(n)
        ),
    ),
    "many directory changes": (10, lambda n: "; ".join(f"cd d{i}" for i in range(n))),
    "a directory spelled from many variables": (
        6,
        lambda n: (
            "; ".join(f"x{i}=a || x{i}=b" for i in range(n))
            + "; cd "
            + "".join(f"$x{i}" for i in range(n))
            + " && echo hi > f"
        ),
    ),
    "a directory spelled many ways": (
        8,
        lambda n: (
            "; ".join(f"x{i}=/. || x{i}=/./" for i in range(n))
            + "; cd a"
            + "".join(f"$x{i}" for i in range(n))
            + " && echo hi > f"
        ),
    ),
    "many commits": (25, lambda n: "; ".join(f"git commit -m m{i}" for i in range(n))),
    "many distinct substitutions": (50, lambda n: "echo " + " ".join(f"$(a{i})" for i in range(n))),
    "many equal substitutions": (50, lambda n: "echo " + " ".join("$(a)" for _ in range(n))),
    "many function substitutions": (50, lambda n: "echo " + " ".join("${ :; }" for _ in range(n))),
    "many backticks": (50, lambda n: "echo " + " ".join(f"`a{i}`" for i in range(n))),
    "a run of backticks": (500, lambda n: "echo " + "`" * n),
    "one word of many substitutions": (50, lambda n: "echo " + "$()" * n),
    "many case arms": (
        50,
        lambda n: "case x in " + " ".join(f"a{i}) :;;" for i in range(n)) + " esac",
    ),
    "a body of expansions": (
        1_000,
        lambda n: "cat <<E\n" + _filled("p $5 $HOME ${n} $$\n", n) + "\nE",
    ),
    "a body of continued lines": (1_000, lambda n: "cat <<E\n" + "x\\\n" * (n // 3) + "\nE"),
    "a chain of wrappers": (25, lambda n: "sudo " * n + "echo x"),
    "a chain of wrappers that change directory": (10, lambda n: "sudo -D d " * n + "echo x"),
    "a chain of wrappers that change to many directories": (
        10,
        lambda n: "".join(f"sudo -D d{i} " for i in range(n)) + "echo x",
    ),
    "many assignments after env": (
        25,
        lambda n: "env " + " ".join(f"A{i}=1" for i in range(n)) + " echo x",
    ),
    "many commands run by find": (
        25,
        lambda n: "find . " + " ".join(f"-exec echo {i} ;" for i in range(n)),
    ),
    "many git options": (
        25,
        lambda n: "git " + " ".join(f"-c a.b{i}=c" for i in range(n)) + " status",
    ),
    "many words after git": (2_000, lambda n: "echo " + " ".join(["git"] * n)),
}
INSIDE_ONE_CALL = frozenset({"many words after git"})
TIMED = 8.0
SQUARED = 4.5
SQUARE = frozenset(
    {
        "unclosed substitutions in a body",
        "unclosed substitutions on the line",
        "nested substitutions",
        "nested substitutions over lines",
        "nested heredocs",
        "words piped into a shell",
        "many directory changes",
        "a chain of wrappers that change to many directories",
    }
)


def work(command: str) -> int:
    calls = [0]

    def count(_frame: FrameType, event: str, _arg: object) -> None:
        calls[0] += event in {"call", "c_call"}

    guard = make_guard()
    re.purge()
    sys.setprofile(count)
    try:
        bash.check(command, Context(ROOT), guard)
    finally:
        sys.setprofile(None)
    return calls[0]


def seconds_grew(small: str, large: str) -> float:
    guard = make_guard()
    bash.check(small, Context(ROOT), guard)
    spent: tuple[list[float], list[float]] = ([], [])
    for _ in range(5):
        for side, command in zip(spent, (small, large), strict=True):
            start = time.process_time()
            bash.check(command, Context(ROOT), guard)
            side.append(time.process_time() - start)
    return min(spent[1]) / min(spent[0])


@pytest.mark.parametrize("name", [name for name in FAMILIES if name not in INSIDE_ONE_CALL])
def test_the_guards_work_grows_no_faster_than_its_input(name: str) -> None:
    factor = SQUARED if name in SQUARE else LINEAR
    base, make = FAMILIES[name]
    work(make(base))
    earlier = work(make(base))
    for scale in (2, 4):
        later = work(make(base * scale))
        grew = later / earlier
        assert grew <= factor, f"{name}: work grew {grew:.3f} at {base * scale}, over {factor}"
        earlier = later


@pytest.mark.parametrize("name", sorted(INSIDE_ONE_CALL))
def test_the_guards_time_grows_no_faster_than_its_input(name: str) -> None:
    base, make = FAMILIES[name]
    grew = seconds_grew(make(base), make(base * 4))
    assert grew <= TIMED, f"{name}: time grew {grew:.3f} from {base} to {base * 4}, over {TIMED}"
