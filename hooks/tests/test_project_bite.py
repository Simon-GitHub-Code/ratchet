"""``project_bite.py``: every gate a repository bound goes red on its planted fault."""

import os
import tempfile
from pathlib import Path

import pytest

import gatelib
import project_bite
from tests import repos
from tests.repos import branch, commit, git, repository, write

TABLE = "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
# A gate is a case of one shell script: red when a file under src/ holds the word `planted`.
LINT = 'if grep -rl planted src; then echo "L001 a planted fault"; exit 1; fi'
TESTS = 'ls tests/*.t >/dev/null 2>&1 || { echo "no test to run"; exit 1; }'
PLANT = 'write = { path = "src/_bite.txt", content = "planted\\n" }'
MANIFEST = (
    '[[group]]\nname = "tool-config"\nreason = "The rules of a gate."\npaths = ["lint.cfg"]\n'
)


def bite(gate: str, name: str, fault: str = PLANT, *, expect: str = "L001") -> str:
    return f'[[bite]]\ngate = "{gate}"\nname = "{name}"\n{fault}\nexpect = "{expect}"\n'


def row(name: str, tier: str = "0", tool: str = "a tool") -> str:
    return f"| {name} | {tool} | {tier} | | `sh gate {name}` |\n"


def project(
    root: Path, gates: dict[str, str], registry: str | None, *, rows: str | None = None
) -> Path:
    """A repository whose binding binds each gate of ``gates``, a shell body, to a tool."""
    cases = "".join(f"{name})\n{body}\n;;\n" for name, body in gates.items())
    table = TABLE + (rows if rows is not None else "".join(row(name) for name in gates))
    files = {
        "docs/process/binding.md": f"# Binding\n\n## Gates\n\n{table}\n## Speed\n\n| a | b |\n",
        "gate": f'#!/bin/sh\ncase "$1" in\n{cases}esac\n',
        "src/code.txt": "clean\n",
        "tests/one.t": "a test\n",
        ".gitignore": "ignored/\n*.secret\n",
    }
    if registry is not None:
        files["quality/bites.toml"] = registry
    root.mkdir(exist_ok=True)
    return repository(root, files)


def prove(root: Path, *flags: str) -> int:
    return project_bite.main(["project_bite.py", "--root", str(root), "--jobs", "1", *flags])


def state(root: Path) -> tuple[str, str, list[str]]:
    paths = sorted(str(path) for path in root.rglob("*") if ".git" not in path.parts)
    return git(root, "status", "--porcelain"), git(root, "rev-parse", "HEAD"), paths


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scratch copies land in a directory the test can look into, and no outer bite is running."""
    directory = tmp_path / "scratch"
    directory.mkdir()
    repos.skeleton()  # made once per process, in the real temp directory, before it is redirected
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    monkeypatch.delenv(project_bite.BITING, raising=False)
    return directory


def test_a_gate_that_goes_red_on_its_fault_bites_and_the_real_tree_is_untouched(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    write(root, "untracked.txt", "seen by git\n")
    before = state(root)
    assert prove(root) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "bite: lint: a planted word: bites"
    assert out[1].startswith("bite: green: 1 of 1 faults bit, over 1 gates in ")
    assert state(root) == before
    assert list(scratch.iterdir()) == []


def test_each_way_a_gate_fails_to_bite_is_named_and_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {
        "asleep": "echo fine",
        "broken": "echo broken from the start; exit 1",
        "vague": "if grep -rl planted src; then echo something else; exit 1; fi",
        "echoing": f"echo L001 is what I look for\n{LINT.replace('L001 ', '')}",
        "absent": "exit 0",
    }
    rows = "".join(row(name) for name in gates).replace("`sh gate absent`", "`no-such-program x`")
    registry = "".join(bite(name, "a planted word") for name in gates).replace('"absent"', '"x"')
    root = project(tmp_path / "repo", gates, registry, rows=rows)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: asleep: a planted word: DOES NOT BITE\n  the gate is green with the fault" in out
    assert "bite: broken: a planted word: RED BEFORE THE FAULT\n  broken from the start" in out
    assert "bite: vague: a planted word: RED FOR ANOTHER REASON\n" in out
    assert (
        "  the gate is red, and its output lacks 'L001'\n  src/_bite.txt\n  something else" in out
    )
    assert "  the gate is red, and 'L001' is in its output as often as when it is green" in out
    assert "bite: x: a planted word: RED BEFORE THE FAULT\n  cannot run no-such-program" in out
    assert "bite: red: 0 of 5 faults bit, over 5 gates in " in out


def test_words_a_recipe_echoes_on_every_run_count_only_when_the_fault_adds_to_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"lint": f"echo the recipe says L001\n{LINT}"}
    root = project(tmp_path / "repo", gates, bite("lint", "a planted word"))
    assert prove(root) == 0
    assert "bite: lint: a planted word: bites" in capsys.readouterr().out


def test_an_emptied_collection_is_a_fault_and_comes_back_for_the_next_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"tests": TESTS, "after": "test -f tests/one.t && test -f top.t && test -d deep/er"}
    registry = (
        bite("tests", "no test", 'remove = ["tests/**"]', expect="no test to run")
        + bite(
            "tests", "no directory", 'remove = ["tests/**", "top.t", "deep/**"]', expect="no test"
        )
        + bite("after", "no file at the top", 'remove = ["top.t"]', expect="x")
    )
    root = project(tmp_path / "repo", gates, registry)
    write(root, "top.t", "at the top\n")
    write(root, "deep/er/two.t", "nested\n")
    write(root, "deep/keep.txt", "stays\n")
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: tests: no test: bites" in out
    assert "bite: tests: no directory: bites" in out
    assert "bite: after: no file at the top: RED FOR ANOTHER REASON" in out


def test_a_fault_that_cannot_be_planted_is_red_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "repo", "quality/bites/bad.patch", "not a diff\n")
    registry = (
        bite("lint", "no such file", 'remove = ["benchmarks/**"]')
        + bite("lint", "a broken patch", 'patch = "quality/bites/bad.patch"')
        + bite("lint", "an ignored file", 'write = { path = "ignored/x.txt", content = "planted" }')
        + bite("lint", "a planted word")
    )
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: no such file: THE FAULT WAS NOT PLANTED\n" in out
    assert (
        "  no file matches benchmarks/**: the collection is empty already, and the gate was" in out
    )
    assert "bite: lint: a broken patch: THE FAULT WAS NOT PLANTED\n  git apply" in out
    assert (
        "bite: lint: an ignored file: THE FAULT WAS NOT PLANTED\n  ignored/x.txt is ignored" in out
    )
    assert "bite: lint: a planted word: bites" in out


def test_a_patch_and_a_new_file_are_changes_git_sees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patch = (
        "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1 +1 @@\n-clean\n+changed\n"
        "--- /dev/null\n+++ b/src/new.txt\n@@ -0,0 +1 @@\n+new\n"
    )
    gates = {"diff": "git diff --name-only | grep src/ && exit 1; exit 0"}
    registry = bite(
        "diff", "two files", 'patch = "quality/bites/two.patch"', expect="src/new.txt"
    ) + bite(
        "diff",
        "one new file",
        'write = { path = "src/more.txt", content = ["a", "b\\n"] }',
        expect="more",
    )
    write(tmp_path / "repo", "quality/bites/two.patch", patch)
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root) == 0
    assert capsys.readouterr().out.count(": bites\n") == 2


def test_a_committed_fault_is_in_history_for_one_run_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {
        "history": 'git log -1 --format=%s | grep "test(bite)" && exit 1\n'
        "git diff --cached --quiet",
        "later": f'test "$(git rev-list --count HEAD)" = 1 && test -z "$(git status --porcelain)"\n'
        f"{LINT}",
    }
    registry = bite("history", "a commit", f"{PLANT}\ncommit = true", expect="test(bite): a commit")
    root = project(tmp_path / "repo", gates, registry + bite("later", "a planted word"))
    assert prove(root) == 0
    assert capsys.readouterr().out.count(": bites\n") == 2


def test_what_a_gate_leaves_in_the_copy_is_gone_before_the_next_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {
        "messy": f"echo residue > left.txt; echo more >> src/code.txt; mkdir -p ignored\n"
        f"echo kept > ignored/cache\n{LINT}",
        "tidy": f'test ! -e left.txt && test "$(cat src/code.txt)" = clean && test -f ignored/cache'
        f"\n{LINT}",
    }
    registry = bite("messy", "a planted word") + bite("tidy", "a planted word")
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root) == 0
    assert capsys.readouterr().out.count(": bites\n") == 2


def test_args_are_appended_and_each_set_of_args_has_its_own_green_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"fuzz": f'test "$2" = red && {{ echo "refused $2"; exit 1; }}\n{LINT}'}
    registry = (
        bite("fuzz", "a planted word")
        + bite("fuzz", "with seconds", f'{PLANT}\nargs = ["5"]')
        + bite("fuzz", "red from the start", f'{PLANT}\nargs = ["red"]')
    )
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert out.count(": bites\n") == 2
    assert "bite: fuzz: red from the start: RED BEFORE THE FAULT\n  refused red" in out


def test_a_copy_holds_what_git_sees_and_setup_and_include_supply_the_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    needs = "test -f built.secret && test -f ignored/data/a.txt && test -f env.secret"
    gates = {
        "lint": f"{needs} && test ! -e other.secret && test -L link && test ! -e gone.txt\n{LINT}"
    }
    registry = (
        'setup = ["touch built.secret"]\n'
        'include = ["ignored/data", "env.secret", "missing.secret"]\n'
    )
    root = project(tmp_path / "repo", gates, registry + bite("lint", "a planted word"))
    write(root, "ignored/data/a.txt", "a\n")
    write(root, "env.secret", "KEY=1\n")
    write(root, "other.secret", "not for the copy\n")
    write(root, "gone.txt", "tracked, then deleted\n")
    (root / "link").symlink_to("src/code.txt")
    commit(root, "feat(core): a link and a file")
    (root / "gone.txt").unlink()
    assert prove(root) == 0
    assert "bite: lint: a planted word: bites" in capsys.readouterr().out


def test_a_setup_that_fails_is_red_and_plants_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = "setup = [\"sh -c 'echo no lockfile; exit 3'\"]\n" + bite("lint", "a planted word")
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "setup `sh -c 'echo no lockfile; exit 3'` exited 3 in the scratch copy" in out
    assert out.endswith("no lockfile\nbite: red\n")


def test_a_detached_head_a_remote_head_and_a_linked_worktree_are_copied_as_they_are(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {
        "refs": "git rev-parse -q --verify origin/HEAD >/dev/null || exit 1\n"
        "git symbolic-ref -q HEAD && exit 1\n"
        f"{LINT}"
    }
    root = project(tmp_path / "repo", gates, bite("refs", "a planted word"))
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    linked = tmp_path / "linked"
    git(root, "worktree", "add", "-q", "--detach", str(linked))
    assert prove(linked) == 0
    assert "bite: refs: a planted word: bites" in capsys.readouterr().out


def test_lanes_run_at_once_each_in_a_copy_of_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = dict.fromkeys(
        ("one", "two", "three"),
        f"test -e src/_bite.txt || {{ touch started; sleep 0.2; test -e started; }}\n{LINT}",
    )
    registry = "".join(bite(name, "a planted word") for name in gates)
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root, "--jobs", "2") == 0
    out = capsys.readouterr().out
    assert out.count(": bites\n") == 3
    assert prove(root, "--jobs", "0") == 0


def test_one_gate_is_proven_alone_before_the_registry_is_whole(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"lint": LINT, "unproven": "exit 0"}
    root = project(tmp_path / "repo", gates, bite("lint", "a planted word"))
    assert prove(root) == 1
    assert (
        "gate 'unproven' (unproven) is bound to a tool and no [[bite]]" in capsys.readouterr().out
    )
    assert prove(root, "--gate", "lint") == 0
    assert "bite: green: 1 of 1 faults bit, over 1 gates" in capsys.readouterr().out
    assert prove(root, "--gate", "unproven") == 1
    assert capsys.readouterr().out == (
        "no bite to run: a gate with nothing to run is red\nbite: red\n"
    )


def changed(root: Path) -> Path:
    """A branch off the project's ``main``, which holds a manifest."""
    write(root, "guards/manifest.toml", MANIFEST)
    write(root, "lint.cfg", "rules = all\n")
    commit(root, "feat(core): a manifest")
    branch(root)
    return root


def test_a_pull_request_that_touches_no_gate_proves_nothing(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = changed(project(tmp_path / "repo", {"broken": "exit 1"}, bite("broken", "a word")))
    write(root, "src/more.txt", "product code\n")
    write(root, "quality/thresholds.toml", "[coverage]\nlines = 90\n")
    commit(root, "feat(core): more")
    assert prove(root, "--changed") == 0
    assert capsys.readouterr().out == (
        "bite: green: the change touches no path that defines a gate; nothing to prove\n"
    )
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    "path", ["lint.cfg", "quality/bites.toml", "quality/bites/new.patch", "docs/process/binding.md"]
)
def test_a_pull_request_that_touches_a_gate_proves_the_gates_of_tiers_0_and_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    gates = {"lint": LINT, "tests": LINT, "slow": "exit 1"}
    rows = row("lint") + row("tests", "1") + row("slow", "2")
    registry = "".join(bite(name, "a planted word") for name in gates)
    root = changed(project(tmp_path / "repo", gates, registry, rows=rows))
    write(root, path, (gatelib.read_text(root / path) or "") + "\n")
    assert prove(root, "--changed") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"bite: a gate may have changed ({path})"
    assert out[1:3] == ["bite: lint: a planted word: bites", "bite: tests: a planted word: bites"]
    assert out[3].startswith("bite: green: 2 of 2 faults bit, over 2 gates")


def test_a_pull_request_with_nothing_to_compare_with_proves_the_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    assert prove(root, "--changed") == 0
    assert "bite: a gate may have changed (no merge base)\n" in capsys.readouterr().out


def test_a_bite_of_the_gate_that_runs_the_bites_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    monkeypatch.setenv(project_bite.BITING, "1")
    assert prove(root) == 1
    assert capsys.readouterr().out == (
        "a bite cannot plant a fault for the gate that runs it\nbite: red\n"
    )


def test_a_gate_in_the_copy_judges_the_copy_and_knows_it_is_being_bitten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    gates = {
        "env": 'test -z "$GIT_DIR$GIT_INDEX_FILE$VIRTUAL_ENV" && test "$RATCHET_BITING" = 1 || '
        f"exit 1\n{LINT}"
    }
    root = project(tmp_path / "repo", gates, bite("env", "a planted word"))
    monkeypatch.setenv("VIRTUAL_ENV", str(root / ".venv"))
    assert prove(root) == 0
    assert "bite: env: a planted word: bites" in capsys.readouterr().out


def test_an_interrupt_stops_the_gates_and_removes_the_copies(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"slow": "kill -TERM $PPID; sleep 60", "next": LINT}
    registry = bite("slow", "a planted word") + bite("next", "a planted word")
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root) == 128 + 15
    out = capsys.readouterr().out
    assert out.endswith("bite: red: interrupted by signal 15\n")
    assert "bite: next" not in out
    assert list(scratch.iterdir()) == []


def test_a_stopped_shell_runs_nothing_more(tmp_path: Path) -> None:
    shell = project_bite.Shell()
    assert shell.run(["sh", "-c", "echo out; echo err >&2; exit 4"], tmp_path) == (4, "out\nerr\n")
    shell.stop()
    assert shell.run(["sh", "-c", "exit 0"], tmp_path) == (130, "stopped")


def test_a_repository_with_no_commit_cannot_be_copied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    git(tmp_path, "init", "-q", "-b", "main", str(root))
    write(root, "docs/process/binding.md", f"# Binding\n\n## Gates\n\n{TABLE}{row('lint')}")
    write(root, "quality/bites.toml", bite("lint", "a planted word"))
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "git rev-parse HEAD failed" in out
    assert out.endswith("bite: red\n")


ROWS = (
    row("lint")
    + "| short | a tool |\n"
    + row("format", tool="none: nothing to format; ADR 0001")
    + row("markers", tool="`ratchet` plugin `scripts/markers.py`")
    + row("shipped", tool="`ratchet` plugin `scripts/bite.py` and `scripts/project_bite.py`")
    + row("pins", tool="uv and `ratchet` plugin `scripts/pins.py`")
    + row("unbound", tool="")
    + "| no entry | a tool | 2 | | |\n"
)


def test_the_gates_table_is_read_row_by_row_and_a_tool_the_repository_chose_is_bound() -> None:
    rows = project_bite.gate_rows(f"# Binding\n\n## Gates\n\n{TABLE}{ROWS}\n## Speed\n\n| a |\n")
    assert [(r.gate, r.bound) for r in rows] == [
        ("lint", True),
        ("format", False),
        ("markers", False),
        ("shipped", False),
        ("pins", True),
        ("unbound", False),
        ("", True),
    ]
    assert rows[0] == project_bite.Row("lint", "a tool", "0", "sh gate lint")
    assert project_bite.gate_rows("# Binding\n\nNo table.\n") == []


@pytest.mark.parametrize(
    ("registry", "problem"),
    [
        ("[[bite]\n", "Expected"),
        ('speed = "fast"\n', "unknown key 'speed'"),
        ("bite = 3\n", "'bite' is written as [[bite]] tables"),
        ("bite = [3]\n", "'bite' is written as [[bite]] tables"),
        ("setup = [3]\n", "'setup' is a list of non-empty strings"),
        ('setup = "make"\n', "'setup' is a list of non-empty strings"),
        ('include = ["../outside"]\n', "'include' names paths inside the repository"),
        (bite("lint", "x") + "tier = 0\n", "bite 1 (lint): unknown key 'tier'"),
        (bite("lint", "x") + 'commit = "yes"\n', "bite 1 (lint): 'commit' is true or false"),
        (bite("lint", "x") + 'args = "5"\n', "bite 1 (lint): 'args' is a list of non-empty"),
        (bite("lint", ""), "bite 1 (lint): 'name' is a non-empty string"),
        (bite("lint", "x", expect=" "), "bite 1 (lint): 'expect' is a non-empty string"),
        (f'[[bite]]\nname = "x"\n{PLANT}\nexpect = "y"\n', "bite 1 (?): 'gate' is a non-empty"),
        (bite("lint", "x", ""), "bite 1 (lint): a bite plants exactly one fault"),
        (bite("lint", "x", f'{PLANT}\nremove = ["a"]'), "bite 1 (lint): a bite plants exactly one"),
        (bite("lint", "x", "remove = []"), "bite 1 (lint): 'remove' names no glob"),
        (bite("lint", "x", 'remove = "tests"'), "bite 1 (lint): 'remove' is a list of non-empty"),
        (bite("lint", "x", 'patch = "fault.patch"'), "'patch' is a path under quality/bites/"),
        (bite("lint", "x", 'patch = "quality/bites/../../x"'), "'patch' is a path under quality/"),
        (bite("lint", "x", "patch = 3"), "bite 1 (lint): 'patch' is a non-empty string"),
        (bite("lint", "x", 'write = "src/a.txt"'), "'write' is { path = ..., content = ... }"),
        (bite("lint", "x", 'write = { path = "a" }'), "'write' is { path = ..., content = ... }"),
        (
            bite("lint", "x", 'write = { path = "/etc/passwd", content = "x" }'),
            "'write' names a path inside the repository",
        ),
        (
            bite("lint", "x", 'write = { path = "a", content = [1] }'),
            "'content' is a list of non-empty strings",
        ),
        (bite("lint", "x", 'write = { path = "", content = "x" }'), "'path' is a non-empty string"),
    ],
)
def test_a_registry_that_is_malformed_says_where(registry: str, problem: str) -> None:
    _, problems = project_bite.read_registry(registry)
    assert len(problems) == 1
    assert problem in problems[0]


def test_a_well_formed_bite_is_read_whole_and_a_malformed_one_is_left_out() -> None:
    source = (
        'setup = ["make deps"]\n'
        + bite("lint", "x", 'write = { path = "a.txt", content = ["-----BEGIN ", "KEY-----\\n"] }')
        + 'args = ["5"]\ncommit = true\n'
        + bite("lint", "y", "patch = 3")
        + bite("perf", "z", 'remove = ["bench/**"]')
    )
    registry, problems = project_bite.read_registry(source)
    assert registry.setup == ("make deps",)
    assert registry.bites == (
        project_bite.Bite(
            "lint",
            "x",
            "L001",
            project_bite.Write("a.txt", "-----BEGIN KEY-----\n"),
            args=("5",),
            commit=True,
        ),
        project_bite.Bite("perf", "z", "L001", project_bite.Remove(("bench/**",))),
    )
    (claimed,) = project_bite.read_registry(bite("lint", "x", PLANT + '\nproof = "a-proof"'))[
        0
    ].bites
    assert claimed.proof == "a-proof"
    assert problems == ["bite 2 (lint): 'patch' is a non-empty string"]
    kept, problems = project_bite.read_registry("setup = [3]\n" + bite("lint", "x"))
    assert [b.name for b in kept.bites] == ["x"]
    assert problems == ["'setup' is a list of non-empty strings"]


def test_conformance_hears_of_every_gap_in_the_registry(tmp_path: Path) -> None:
    rows = (
        row("lint")
        + row("unit, property and fitness tests").replace("`sh gate unit", "`sh gate tests")
        + row("performance budget").replace("`sh gate performance budget`", "`sh gate perf`")
        + row("format", tool="none: nothing to format; ADR 0001")
    )
    rows = rows.replace("`sh gate tests, property and fitness tests`", "`sh gate tests`")
    registry = (
        bite("lint", "x")
        + bite("lint", "x")
        + bite("lnit", "y")
        + bite("lint", "z", 'patch = "quality/bites/none.patch"')
        + bite("tests", "a failing test")
        + bite("perf", "slow")
        + bite("perf", "no benchmark", 'remove = ["bench/**"]')
    )
    root = project(tmp_path / "repo", {}, registry, rows=rows)
    assert project_bite.failures(root) == [
        "quality/bites.toml: gate 'lint' has two bites named 'x'",
        "quality/bites.toml: bite 'y' names gate 'lnit', which is no row's entry point",
        "quality/bites.toml: bite 'z' applies quality/bites/none.patch, which is missing",
        (
            "quality/bites.toml: gate 'tests' (unit, property and fitness tests) judges a "
            "collection and no [[bite]] uses 'remove' to show it red when the collection is empty"
        ),
    ]
    (root / "quality/bites.toml").unlink()
    assert project_bite.failures(root) == [
        "quality/bites.toml: missing; every gate bound to a tool needs a [[bite]]"
    ]
    (root / "docs/process/binding.md").unlink()
    assert project_bite.failures(root) == [
        "quality/bites.toml: missing; every gate bound to a tool needs a [[bite]]"
    ]


def test_a_question_that_requires_a_proof_is_unproven_until_a_bite_claims_it(
    tmp_path: Path,
) -> None:
    rows = (
        row("telemetry emitted", "1").replace("`sh gate telemetry emitted`", "`sh gate telemetry`")
        + row("load smoke", "1", tool="none: nothing to boot; ADR 0001")
        + row("changed flags off and on", "1").replace(
            "gate changed flags off and on", "gate flags"
        )
    )
    registry = (
        bite("telemetry", "a failing test")
        + bite("telemetry", "no test", 'remove = ["tests/**"]')
        + bite("flags", "a broken state", PLANT + '\nproof = "broken-flag-state"')
    )
    root = project(tmp_path / "repo", {}, registry, rows=rows)
    assert project_bite.failures(root) == [
        (
            "quality/bites.toml: gate 'telemetry' (telemetry emitted) has no [[bite]] with proof ="
            ' "outside-the-seam": an entry point registered outside the instrumented seam, which'
            " emits nothing"
        )
    ]
    claimed = bite("telemetry", "past the seam", PLANT + '\nproof = "outside-the-seam"')
    write(root, "quality/bites.toml", registry + claimed)
    assert project_bite.failures(root) == []
    write(root, "quality/bites.toml", registry + claimed.replace('"outside-the-seam"', "3"))
    assert project_bite.failures(root)[0] == (
        "quality/bites.toml: bite 4 (telemetry): 'proof' is a non-empty string"
    )


def test_the_scripts_own_environment_names_no_other_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    assert "GIT_DIR" not in project_bite.environment()
    assert project_bite.environment()["PATH"] == os.environ["PATH"]


@pytest.mark.parametrize("path", ["link/planted.txt", "file-link"])
def test_a_write_fault_does_not_follow_a_tracked_link_out_of_the_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    # Why: the copy carries a tracked link as a link, and a write through it landed outside the
    # copy, where restoring the copy does not reach.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("the owner's\n", encoding="utf-8")
    fault = f'write = {{ path = "{path}", content = "planted\\n" }}'
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "through a link", fault))
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "file-link").symlink_to(outside / "kept.txt")
    commit(root, "feat(core): two links out of the repository")
    assert prove(root) == 1
    assert f"{path} leaves the copy through a symbolic link" in capsys.readouterr().out
    assert sorted(p.name for p in outside.iterdir()) == ["kept.txt"]
    assert (outside / "kept.txt").read_text(encoding="utf-8") == "the owner's\n"


def test_a_write_fault_through_a_link_that_stays_in_the_copy_is_planted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fault = 'write = { path = "alias.txt", content = "planted\\n" }'
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "through a link", fault))
    (root / "alias.txt").symlink_to("src/code.txt")
    commit(root, "feat(core): a link inside the repository")
    before = state(root)
    assert prove(root) == 0
    assert "bite: lint: through a link: bites" in capsys.readouterr().out
    assert state(root) == before


@pytest.mark.parametrize("include", ["ignored/out", "ignored/out/kept.txt", "env.secret"])
def test_an_include_that_leaves_the_repository_through_a_link_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], include: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("not the repository's\n", encoding="utf-8")
    registry = f'include = ["{include}"]\n' + bite("lint", "a planted word")
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    (root / "ignored").mkdir()
    (root / "ignored/out").symlink_to(outside, target_is_directory=True)
    (root / "env.secret").symlink_to(outside / "kept.txt")
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert f"include '{include}' leaves the repository through a symbolic link" in out
    assert "bites" not in out
