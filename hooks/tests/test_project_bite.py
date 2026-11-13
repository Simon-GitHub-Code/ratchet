"""``project_bite.py``: every gate a repository bound goes red on its planted fault."""

import contextlib
import os
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import gatelib
import project_bite
from tests import repos
from tests.repos import branch, commit, git, repository, write
from tests.servers import gone

TABLE = "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
# A gate is a case of one shell script: red when a file under src/ holds the word `planted`.
LINT = 'if grep -rl planted src; then echo "L001 a planted fault"; exit 1; fi'
TESTS = 'ls tests/*.t >/dev/null 2>&1 || { echo "no test to run"; exit 1; }'
PLANT = 'write = { path = "src/_bite.txt", content = "planted\\n" }'
# Seconds from a signal to the end of the proof, on a shared machine: the runner wakes every tenth
# of a second, and the gate's group ends at once on SIGTERM.
STOPS_WITHIN = 3
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
        + bite("after", "a directory that keeps one", 'remove = ["deep/keep.txt"]', expect="x")
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
    assert "bite: after: a directory that keeps one: DOES NOT BITE" in out


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


def test_a_replace_keeps_biting_after_an_unrelated_edit_breaks_the_same_patch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: observed in a dogfood run, 2026-11-13: a package added to package.json broke both
    # context patches of the pins gate, and the owner was stopped to repair them.
    lines = "".join(f"line {n}\n" for n in range(8))
    patch = "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1,4 +1,4 @@\n line 0\n-line 1\n+planted\n"
    patch += " line 2\n line 3\n"
    registry = bite(
        "lint",
        "by replace",
        'replace = { path = "src/code.txt", old = "line 1\\n", new = "planted\\n" }',
    ) + bite("lint", "by patch", 'patch = "quality/bites/p.patch"')
    write(tmp_path / "repo", "quality/bites/p.patch", patch)
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    write(root, "src/code.txt", lines)
    commit(root, "feat(core): eight lines")
    assert prove(root) == 0
    assert capsys.readouterr().out.count(": bites\n") == 2
    write(root, "src/code.txt", lines.replace("line 2\n", "line 2\nan unrelated line\n"))
    commit(root, "feat(core): an unrelated line")
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: by replace: bites\n" in out
    assert "bite: lint: by patch: THE FAULT WAS NOT PLANTED\n  git apply" in out


def test_a_replace_may_change_several_places_and_leaves_the_tree_as_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fault = (
        'replace = [{ path = "src/code.txt", old = "clean", new = "plan" },'
        ' { path = "src/code.txt", old = "plan\\n", new = "planted\\n" }]'
    )
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "two steps", fault))
    before = state(root)
    assert prove(root) == 0
    assert "bite: lint: two steps: bites" in capsys.readouterr().out
    assert state(root) == before


@pytest.mark.parametrize(
    "case",
    [
        ('path = "src/code.txt", old = "absent", new = "x"', "src/code.txt no longer holds"),
        ('path = "gate", old = "planted", new = "x"', "in more than one place; lengthen"),
        ('path = "src", old = "clean", new = "x"', "src is not a file in the copy"),
        ('path = "nowhere.txt", old = "clean", new = "x"', "nowhere.txt is not a file in the copy"),
        ('path = "ignored/x.txt", old = "clean", new = "x"', "ignored/x.txt is ignored by git"),
        ('path = "dir-link/x.txt", old = "clean", new = "x"', "dir-link/x.txt is reached through"),
        ('path = ".git/config", old = "bare", new = "x"', ".git/config is not a path in the work"),
        ('path = "file-link", old = "clean", new = "x"', "file-link is reached through a symbolic"),
    ],
)
def test_a_replace_that_no_longer_applies_is_red_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str, str]
) -> None:
    swap, says = case
    outside = tmp_path / "outside.txt"
    outside.write_text("clean\n", encoding="utf-8")
    root = project(
        tmp_path / "repo", {"lint": LINT}, bite("lint", "gone", f"replace = {{ {swap} }}")
    )
    (root / "file-link").symlink_to(outside)
    (root / "dir-link").symlink_to("ignored", target_is_directory=True)
    commit(root, "feat(core): a link out of the repository and one to what git ignores")
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: gone: THE FAULT WAS NOT PLANTED\n" in out
    assert says in out
    assert outside.read_text(encoding="utf-8") == "clean\n"


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


def test_a_detached_head_a_remote_head_and_a_linked_worktree_are_copied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"refs": f"git rev-parse -q --verify origin/HEAD >/dev/null || exit 1\n{LINT}"}
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


def test_ratchet_jobs_caps_the_lanes_as_it_caps_the_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gates = dict.fromkeys(("one", "two", "three"), LINT)
    registry = "".join(bite(name, "a planted word") for name in gates)
    root = project(tmp_path / "repo", gates, registry)
    seen: list[int] = []

    def record(_root: Path, _registry: object, _tasks: object, *, lanes: int) -> int:
        seen.append(lanes)
        return 0

    monkeypatch.setattr(project_bite, "proof", record)
    for value in ("1", "2", "64"):
        monkeypatch.setenv("RATCHET_JOBS", value)
        assert project_bite.main(["project_bite.py", "--root", str(root)]) == 0
    assert seen == [1, 2, min(project_bite.MAX_LANES, len(gates))]


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


def lanes() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name.startswith("bite-lane")]


@pytest.mark.parametrize("receiver", ["the main thread", "a lane's thread"])
def test_an_interrupt_stops_the_gates_promptly_whichever_thread_receives_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], receiver: str
) -> None:
    # Why: the kernel hands a process-directed signal to any thread. One handed to a lane's
    # thread waited for the gate to finish, while the main thread slept in a blocking call.
    started, stopped = tmp_path / "started", tmp_path / "stopped"
    # Why: a shell runs a trap only between commands, so the gate waits in short sleeps; one
    # long sleep forked just after the signal would hold the trap back for its whole length. The
    # wait ends by itself a minute on: a test run killed first leaves nothing running for good,
    # and a runner that misses the signal fails this test instead of hanging the suite.
    until = int(time.time()) + 60
    slow = (
        f"trap 'echo ended by the runner > {stopped}; exit 143' TERM\n"
        f'while [ "$(date +%s)" -lt {until} ]; do touch {started}; sleep 0.05; done'
    )
    registry = bite("slow", "a planted word") + bite("next", "a planted word")
    root = project(tmp_path / "repo", {"slow": slow, "next": LINT}, registry)
    sent: list[float] = []

    def interrupt() -> None:
        while not started.exists():
            time.sleep(0.01)
        target = threading.main_thread() if receiver == "the main thread" else lanes()[0]
        sent.append(time.monotonic())
        signal.pthread_kill(target.ident or 0, signal.SIGTERM)

    threading.Thread(target=interrupt, daemon=True).start()
    assert prove(root) == 128 + 15
    assert time.monotonic() - sent[0] < STOPS_WITHIN
    out = capsys.readouterr().out
    assert out.endswith("bite: red: interrupted by signal 15\n")
    assert "bite: next" not in out
    assert stopped.read_text(encoding="utf-8") == "ended by the runner\n"
    assert lanes() == []
    assert list((tmp_path / "scratch").iterdir()) == []


def test_after_an_interrupt_the_gate_it_stopped_runs_no_other_bite_and_prints_no_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: a stopped lane still walked its gate's other bites, restoring the copy after each and
    # printing a verdict for it, for seconds after the stop with hundreds of bites.
    started = tmp_path / "started"
    registry = "".join(bite("slow", f"bite {n}") for n in range(1000))
    # Why: a runner that misses the signal gets a gate that waits a minute at most and is then
    # red before every fault, so the test fails in seconds more instead of hanging the suite.
    until = int(time.time()) + 60
    slow = (
        f'[ "$(date +%s)" -ge {until} ] && exit 1\ntouch {started}\n'
        f'while [ "$(date +%s)" -lt {until} ]; do sleep 0.05; done'
    )
    root = project(tmp_path / "repo", {"slow": slow}, registry)
    sent: list[float] = []

    def interrupt() -> None:
        while not started.exists():
            time.sleep(0.01)
        sent.append(time.monotonic())
        signal.pthread_kill(threading.main_thread().ident or 0, signal.SIGTERM)

    threading.Thread(target=interrupt, daemon=True).start()
    assert prove(root) == 128 + 15
    assert time.monotonic() - sent[0] < STOPS_WITHIN
    assert capsys.readouterr().out == "bite: red: interrupted by signal 15\n"


def test_an_interrupt_while_the_lanes_start_waits_for_every_lane_before_the_copies_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Why: a signal between a lane's start and the pool recording it left a lane the pool did not
    # wait for, writing into a copy that was being removed.
    gates = {"one": LINT, "two": LINT}
    registry = bite("one", "a planted word") + bite("two", "a planted word")
    root = project(tmp_path / "repo", gates, registry)
    start = threading.Thread.start

    def start_then_interrupt(thread: threading.Thread) -> None:
        start(thread)
        if thread.name.startswith("bite-lane") and len(lanes()) == 2:
            signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(threading.Thread, "start", start_then_interrupt)
    assert prove(root, "--jobs", "2") == 128 + 15
    assert capsys.readouterr().out.endswith("bite: red: interrupted by signal 15\n")
    assert lanes() == []
    assert list((tmp_path / "scratch").iterdir()) == []


def test_what_a_gate_leaves_running_ends_with_it_and_cannot_hold_the_run_open(
    tmp_path: Path,
) -> None:
    forked = tmp_path / "forked"
    code, output = project_bite.Shell().run(
        ["sh", "-c", f"sleep 60 & echo $! > {forked}; echo done"], tmp_path
    )
    assert (code, output) == (0, f"done\nbite: {gatelib.LEFT_RUNNING}\n")
    assert gone(int(forked.read_text()))
    assert project_bite.Shell().run(["sh", "-c", "echo tidy"], tmp_path) == (0, "tidy\n")


def test_the_first_copy_is_set_up_alone_so_the_others_find_what_it_cached(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "setups"
    setup = f"sh -c 'echo + >> {log}; sleep 0.2; echo - >> {log}'"
    gates = dict.fromkeys(("one", "two", "three"), LINT)
    registry = f'setup = ["{setup}"]\n' + "".join(bite(name, "a planted word") for name in gates)
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root, "--jobs", "3") == 0
    assert capsys.readouterr().out.count(": bites\n") == 3
    lines = log.read_text(encoding="utf-8").split()
    assert lines[:2] == ["+", "-"]
    assert sorted(lines) == ["+"] * 3 + ["-"] * 3


def test_in_a_copy_the_change_is_what_the_tree_holds_beyond_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: a gate over the change would otherwise judge the whole branch again for every bite.
    gates = {
        "changed": "git symbolic-ref -q HEAD && exit 1\n"
        'test "$(git merge-base HEAD main)" = "$(git rev-parse HEAD)" || exit 1\n'
        'test "$(git merge-base HEAD origin/main)" = "$(git rev-parse HEAD)" || exit 1\n'
        'git status --porcelain | grep -q _bite && echo "L001 the fault is the change" && exit 1\n'
        "exit 0"
    }
    root = project(tmp_path / "repo", gates, bite("changed", "a planted word"))
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    branch(root)
    write(root, "src/more.txt", "the feature\n")
    commit(root, "feat(core): more")
    assert prove(root) == 0
    assert "bite: changed: a planted word: bites" in capsys.readouterr().out
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "change"
    assert git(root, "rev-parse", "main") != git(root, "rev-parse", "HEAD")


def test_the_adoption_proves_nothing_on_a_pull_request_since_tier_2_proves_it_whole(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path / "repo")
    branch(root, "adopt")
    project_files = project(tmp_path / "other", {"lint": LINT}, bite("lint", "a planted word"))
    for path in ("docs/process/binding.md", "gate", "quality/bites.toml", "src/code.txt"):
        write(root, path, (project_files / path).read_text(encoding="utf-8"))
    assert prove(root, "--changed") == 0
    assert capsys.readouterr().out == (
        "bite: green: this change is the adoption, whose base holds no binding; nothing is"
        " proven here, and `bite-all` proves every gate in tier 2\n"
    )
    assert list(scratch.iterdir()) == []
    assert prove(root) == 0


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
    + row("waiting", tool="later: code:listener")
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
        ("waiting", False),
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
        (bite("lint", "x", 'replace = "a.txt"'), "'replace' is { path = ..., old = ..., new = ."),
        (bite("lint", "x", 'replace = [{ path = "a" }]'), "'replace' is { path = ..., old = ."),
        (bite("lint", "x", "replace = []"), "bite 1 (lint): 'replace' names no text"),
        (
            bite("lint", "x", 'replace = { path = "../a", old = "b", new = "c" }'),
            "'replace' names a path inside the repository",
        ),
        (
            bite("lint", "x", 'replace = { path = "a", old = "", new = "c" }'),
            "'old' is a non-empty string",
        ),
        (bite("lint", "x", 'replace = { path = "a", old = "b", new = 3 }'), "'new' is a string"),
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
    absent = project_bite.failures(root)
    assert absent[0] == "quality/bites.toml: missing; every gate bound to a tool needs a [[bite]]"
    assert absent[1:] == [
        f"quality/bites.toml: gate '{gate}' ({question}) is bound to a tool and no [[bite]]"
        " plants a fault for it"
        for gate, question in (
            ("lint", "lint"),
            ("tests", "unit, property and fitness tests"),
            ("perf", "performance budget"),
        )
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
    assert f"{path} is reached through a symbolic link" in capsys.readouterr().out
    assert sorted(p.name for p in outside.iterdir()) == ["kept.txt"]
    assert (outside / "kept.txt").read_text(encoding="utf-8") == "the owner's\n"


def test_a_write_fault_through_a_link_that_stays_in_the_copy_is_not_planted_either(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fault = 'write = { path = "alias.txt", content = "planted\\n" }'
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "through a link", fault))
    (root / "alias.txt").symlink_to("src/code.txt")
    commit(root, "feat(core): a link inside the repository")
    before = state(root)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: through a link: THE FAULT WAS NOT PLANTED\n  alias.txt is reached" in out
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


# A gate that reads one file git ignores: red when it holds the word `planted`.
READS_IGNORED = 'if grep -l planted x.secret 2>/dev/null; then echo "L001 planted"; exit 1; fi'
# A fault this gate cannot see: it bites only if something else left the gate red.
BLIND = 'write = { path = "docs/n.txt", content = "nothing the gate reads\\n" }'


def links_to_a_secret(root: Path) -> None:
    (root / "ign-link").symlink_to("x.secret")
    commit(root, "feat(core): a tracked link to a file git ignores")


def ignores_in_a_nested_gitignore(root: Path) -> None:
    write(root, "docs/.gitignore", "local.txt\n")
    commit(root, "chore(core): a nested ignore rule")


def ignores_in_the_exclude_file(root: Path) -> None:
    with (root / ".git/info/exclude").open("a", encoding="utf-8") as exclude:
        exclude.write("local.txt\n")


def adds_a_submodule(root: Path) -> None:
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    git(root, "commit", "-q", "-m", "feat(core): a submodule")


def links_to_a_tracked_file(root: Path) -> None:
    (root / "alias.txt").symlink_to("src/code.txt")
    commit(root, "feat(core): a tracked link to a tracked file")


@pytest.mark.parametrize(
    "case",
    [
        (
            'write = { path = "ign-link", content = "planted\\n" }',
            links_to_a_secret,
            "ign-link is reached through a symbolic link",
        ),
        (
            'replace = { path = "ign-link", old = "x", new = "y" }',
            links_to_a_secret,
            "ign-link is reached through a symbolic link",
        ),
        (
            'write = { path = "src/.gitignore", content = "*\\nplanted\\n" }',
            None,
            "src/.gitignore would be a new .gitignore",
        ),
        (
            'write = { path = "docs/local.txt", content = "planted\\n" }',
            ignores_in_a_nested_gitignore,
            "docs/local.txt is ignored by git",
        ),
        (
            'write = { path = "local.txt", content = "planted\\n" }',
            ignores_in_the_exclude_file,
            "local.txt is ignored by git",
        ),
        (
            'write = { path = ".git/info/exclude", content = "*\\n" }',
            None,
            ".git/info/exclude is not a path in the work tree",
        ),
        (
            'write = { path = "sub/x.txt", content = "planted\\n" }',
            adds_a_submodule,
            "sub/x.txt is inside sub, a repository of its own",
        ),
        (
            'write = { path = "src/.gitattributes", content = "* text eol=crlf\\n" }',
            None,
            "src/.gitattributes would be a new .gitattributes",
        ),
        (
            'patch = "quality/bites/new-attributes.patch"',
            None,
            ".gitattributes would be a new .gitattributes",
        ),
        ('remove = ["alias.txt"]', links_to_a_tracked_file, "alias.txt is reached through a"),
        ('patch = "quality/bites/new-secret.patch"', None, "x.secret is ignored by git"),
        ('patch = "quality/bites/edit-secret.patch"', None, "env.secret: does not exist in index"),
        (
            'patch = "quality/bites/rename-secret.patch"',
            None,
            "env.secret: does not exist in index",
        ),
    ],
)
def test_a_fault_is_planted_only_in_a_file_git_sees_and_never_through_a_link(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, Callable[[Path], None] | None, str],
) -> None:
    fault, prepare, says = case
    # Why: a write through a tracked link to an ignored file planted the fault where restoring the
    # copy does not reach, and the next bite of the gate, a fault it cannot see, then bit.
    patches = {
        "new-secret": "--- /dev/null\n+++ b/x.secret\n@@ -0,0 +1 @@\n+planted\n",
        "edit-secret": "--- a/env.secret\n+++ b/env.secret\n@@ -1 +1 @@\n-clean\n+planted\n",
        "rename-secret": "diff --git a/env.secret b/x.txt\nsimilarity index 50%\n"
        "rename from env.secret\nrename to x.txt\n--- a/env.secret\n+++ b/x.txt\n"
        "@@ -1 +1 @@\n-clean\n+planted\n",
        # Why: restoring the copy writes src/code.txt back by the attributes it then finds.
        "new-attributes": "--- /dev/null\n+++ b/.gitattributes\n@@ -0,0 +1 @@\n+* text eol=crlf\n"
        "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1 +1 @@\n-clean\n+planted\n",
    }
    for name, text in patches.items():
        write(tmp_path / "repo", f"quality/bites/{name}.patch", text)
    gate = f"{READS_IGNORED}\ngrep -rl planted src env.secret && {{ echo L001; exit 1; }}; exit 0"
    registry = (
        'include = ["env.secret"]\n' + bite("lint", "first", fault) + bite("lint", "blind", BLIND)
    )
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    write(root, "env.secret", "clean\n")
    if prepare is not None:
        prepare(root)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: first: THE FAULT WAS NOT PLANTED\n" in out
    assert says in out
    assert "bite: lint: blind: DOES NOT BITE" in out


def test_a_fault_in_a_file_another_name_shares_stays_in_the_name_it_was_planted_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = "grep -l planted x.secret && { echo L001; exit 1; }; exit 0"
    registry = (
        'setup = ["ln -f src/code.txt x.secret"]\n'
        + bite(
            "lint",
            "by replace",
            'replace = { path = "src/code.txt", old = "clean", new = "planted" }',
        )
        + bite("lint", "by write", 'write = { path = "src/code.txt", content = "planted\\n" }')
    )
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: by replace: DOES NOT BITE" in out
    assert "bite: lint: by write: DOES NOT BITE" in out


# What a bite whose gate was red again with no fault planted says first.
RETURNS = (
    "  the gate did not return to green once the fault was gone, so its red may come from what"
    " its own runs leave and not from the fault"
)


def test_each_bite_is_judged_against_a_green_run_of_its_own_just_before_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: one green run served every bite of a gate, so a gate left red by the first fault
    # (a cache, a mark in an ignored file) made every later fault bite. A gate that stays red
    # after a fault is one red from its second run on, so the first fault is not proven either.
    gate = (
        "test -f ignored/seen && { echo L001 still red; exit 1; }\n"
        "grep -rl planted src && { mkdir -p ignored; touch ignored/seen; echo L001; exit 1; }\n"
        "exit 0"
    )
    registry = bite("lint", "first") + bite("lint", "blind", BLIND)
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert f"bite: lint: first: NOT PROVEN\n{RETURNS}\n  L001 still red" in out
    assert "bite: lint: blind: RED BEFORE THE FAULT\n  L001 still red" in out


@pytest.mark.parametrize("keeps", ["in a directory git ignores", "outside the copy"])
def test_a_gate_red_again_with_no_fault_planted_proves_no_bite_before_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keeps: str
) -> None:
    # Why: a gate red from its second run on, by a count it keeps, was red on every fault, and a
    # fault it never reads bit.
    count = "ignored/runs" if keeps == "in a directory git ignores" else str(tmp_path / "runs")
    gate = (
        f"mkdir -p ignored; n=$(cat {count} 2>/dev/null || echo 0); echo $((n + 1)) > {count}\n"
        'test "$n" -ge 1 && { echo "L001 on run $((n + 1))"; exit 1; }; exit 0'
    )
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "blind", BLIND))
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert f"bite: lint: blind: NOT PROVEN\n{RETURNS}\n  L001 on run 3\n" in out
    assert "bite: red: 0 of 1 faults bit" in out


@pytest.mark.parametrize(
    "case",
    [
        ('replace = { path = "src/code.txt", old = "clean", new = "x" }', "  src/code.txt is not"),
        ('write = { path = "src/other.txt/x", content = "x" }', "Not a directory: 'src/other.txt'"),
        ('remove = ["src/code.txt"]', "  no file matches src/code.txt"),
        ('patch = "quality/bites/p.patch"', "  git apply"),
    ],
)
def test_every_kind_of_fault_that_cannot_be_planted_says_so_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str, str]
) -> None:
    fault, says = case
    # Why: a replace naming a tracked file deleted in the work tree crashed with a traceback.
    patch = "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1 +1 @@\n-clean\n+planted\n"
    write(tmp_path / "repo", "quality/bites/p.patch", patch)
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "gone", fault))
    (root / "src/code.txt").unlink()
    write(root, "src/other.txt", "clean\n")
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: gone: THE FAULT WAS NOT PLANTED\n" in out
    assert says in out


def test_a_file_a_gate_hid_from_git_is_gone_before_the_next_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = (
        "test -e new/x && { echo L001 left behind; exit 1; }\n"
        "mkdir -p new; echo x > new/.gitignore; echo planted > new/x\n"
        f"{LINT}"
    )
    registry = bite("lint", "first") + bite("lint", "second")
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 0
    assert capsys.readouterr().out.count(": bites\n") == 2


def test_a_copy_that_cannot_be_put_back_as_it_stood_makes_the_run_red(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: a later fault would be judged in a copy that holds what an earlier run left.
    gate = f"mkdir -p locked; echo planted > locked/f; chmod 555 locked\n{LINT}"
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "first"))
    try:
        assert prove(root) == 1
    finally:
        for locked in scratch.rglob("locked"):
            locked.chmod(0o755)
    out = capsys.readouterr().out
    assert "warning: failed to remove locked/f" in out
    assert "bites" not in out


# A gate that caches its verdict in an ignored directory, by a checksum of what it reads.
CACHING = (
    'h=$(cat src/* | cksum | cut -d" " -f1); mkdir -p ignored\n'
    "test -f ignored/$h && { cat ignored/$h; test ! -s ignored/$h; exit; }\n"
    "if grep -rl planted src; then echo L001 > ignored/$h; echo L001; exit 1; fi\n"
    ": > ignored/$h"
)
CANCELS = (
    'replace = [{ path = "src/code.txt", old = "clean", new = "planted" },'
    ' { path = "src/code.txt", old = "planted", new = "clean" }]'
)


@pytest.mark.parametrize(
    "case",
    [
        (
            CACHING,
            bite("lint", "first") + bite("lint", "blind", BLIND),
            ["first: bites", "blind: DOES NOT BITE"],
        ),
        (LINT, bite("lint", "cancels", CANCELS), ["cancels: DOES NOT BITE"]),
    ],
)
def test_a_cache_or_faults_that_cancel_get_the_verdict_the_gate_earns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str, str, list[str]]
) -> None:
    gate, registry, verdicts = case
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    for verdict in verdicts:
        assert f"bite: lint: {verdict}" in out


def tracks_an_ignored_file(root: Path) -> None:
    write(root, "kept.secret", "clean\n")
    git(root, "add", "--force", "kept.secret")
    commit(root, "chore(core): a file the index tracks and an ignore rule matches")


def skips_a_present_file(root: Path) -> None:
    write(root, "src/skipped.txt", "clean\n")
    commit(root, "feat(core): a file the index will skip")
    git(root, "update-index", "--skip-worktree", "src/skipped.txt")


def skips_an_absent_file(root: Path) -> None:
    skips_a_present_file(root)
    (root / "src/skipped.txt").unlink()


def deletes_a_tracked_ignored_file(root: Path) -> None:
    # Why: the index tracks it, so git calls it not ignored there; restoring cleans by the
    # snapshot, which never held it, and keeps it as a file an ignore rule matches.
    tracks_an_ignored_file(root)
    (root / "kept.secret").unlink()


@pytest.mark.parametrize(
    "case",
    [
        (
            'setup = ["mkdir -p reports"]\n',
            'test -d reports || { echo "L001 no reports/"; exit 1; }',
            None,
            "RED BEFORE THE FAULT",
        ),
        (
            "",
            'grep -q ran kept.secret && { echo "L001 stale"; exit 1; }; echo ran > kept.secret',
            tracks_an_ignored_file,
            "DOES NOT BITE",
        ),
    ],
    ids=["an empty directory setup made", "a tracked file an ignore rule matches"],
)
def test_the_first_green_run_judges_the_copy_as_every_later_run_does(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str, Callable[[Path], None] | None, str],
) -> None:
    # Why: the first green run judged the copy as setup left it, and every later run the copy as
    # restoring leaves it. Where the two differed, a fault the gate never reads bit.
    setup, gate, prepare, verdict = case
    root = project(
        tmp_path / "repo", {"lint": f"{gate}\nexit 0"}, setup + bite("lint", "blind", BLIND)
    )
    if prepare is not None:
        prepare(root)
    assert prove(root) == 1
    assert f"bite: lint: blind: {verdict}\n" in capsys.readouterr().out


@pytest.mark.parametrize(
    "case",
    [
        (
            'replace = { path = "kept.secret", old = "clean", new = "planted" }',
            "grep -l planted kept.secret && { echo L001; exit 1; }",
            tracks_an_ignored_file,
            "bites",
        ),
        (
            'remove = ["kept.secret"]',
            'test -f kept.secret || { echo "L001 gone"; exit 1; }',
            tracks_an_ignored_file,
            "bites",
        ),
        (
            PLANT,
            'grep -rl planted src && { echo "*" >> .git/info/exclude; echo L001; exit 1; }',
            None,
            "bites",
        ),
        (
            'replace = { path = "src/skipped.txt", old = "clean", new = "planted" }',
            LINT,
            skips_a_present_file,
            "bites",
        ),
        (
            'write = { path = "src/skipped.txt", content = "planted\\n" }',
            LINT,
            skips_an_absent_file,
            "THE FAULT WAS NOT PLANTED",
        ),
        (
            'write = { path = "kept.secret", content = "planted\\n" }',
            "grep -l planted kept.secret && { echo L001; exit 1; }",
            deletes_a_tracked_ignored_file,
            "THE FAULT WAS NOT PLANTED",
        ),
    ],
    ids=[
        "a replace in a tracked file an ignore rule matches",
        "a remove of it",
        "a gate that writes the exclude file",
        "a file the index skips",
        "a file the index skips and the tree lacks",
        "a tracked file an ignore rule matches, deleted from the tree",
    ],
)
def test_what_a_fault_or_its_run_changed_is_put_back_before_the_next_bite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str, Callable[[Path], None] | None, str],
) -> None:
    fault, gate, prepare, verdict = case
    registry = bite("lint", "first", fault) + bite("lint", "blind", BLIND)
    root = project(tmp_path / "repo", {"lint": f"{gate}\nexit 0"}, registry)
    if prepare is not None:
        prepare(root)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert f"bite: lint: first: {verdict}\n" in out
    assert "bite: lint: blind: DOES NOT BITE\n" in out


def test_a_fault_inside_a_repository_setup_made_is_not_planted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: restoring the copy cleans no file inside another repository, so the fault stayed.
    nest = (
        "git init -q vendor/x && echo a > vendor/x/a && git -C vendor/x add a\n"
        "git -C vendor/x -c user.name=a -c user.email=a@example.invalid commit -qm a\n"
    )
    write(tmp_path / "repo", "nest.sh", nest)
    gate = "grep -rl planted vendor && { echo L001; exit 1; }; exit 0"
    fault = 'write = { path = "vendor/x/planted.txt", content = "planted\\n" }'
    registry = (
        'setup = ["sh nest.sh"]\n' + bite("lint", "first", fault) + bite("lint", "blind", BLIND)
    )
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: first: THE FAULT WAS NOT PLANTED\n" in out
    assert "vendor/x/planted.txt is inside vendor/x, a repository of its own" in out
    assert "bite: lint: blind: DOES NOT BITE\n" in out


def fingerprint(root: Path) -> list[tuple[str, int, bytes]]:
    """Every entry of a work tree but ``.git``: its path, its mode, and what it holds."""
    entries = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        mode = path.lstat().st_mode
        held = (
            path.readlink().as_posix().encode()
            if path.is_symlink()
            else path.read_bytes()
            if path.is_file()
            else b""
        )
        entries.append((str(path.relative_to(root)), mode, held))
    return entries


INNER = "src/deep/inner.txt"
SWAPPED = {
    "write": 'write = { path = "src/deep/planted.txt", content = "planted\\n" }',
    "overwrite": f'write = {{ path = "{INNER}", content = "planted\\n" }}',
    "replace": f'replace = {{ path = "{INNER}", old = "clean", new = "planted" }}',
    "remove": f'remove = ["{INNER}"]',
    "patch": 'patch = "quality/bites/inner.patch"',
}


@pytest.mark.parametrize(
    "case",
    [(kind, at) for kind in sorted(SWAPPED) for at in ("src", "src/deep")]
    + [(kind, INNER) for kind in sorted(SWAPPED) if kind != "write"],
)
def test_a_path_swapped_for_a_link_between_the_check_and_the_plant_is_refused(
    tmp_path: Path,
    *,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str],
) -> None:
    kind, swapped = case
    # Why: a process setup left running swapped a directory of the copy for a link to the real
    # tree after the fault's path was checked and before it was written, and the fault landed in
    # the real tree. The swap is made at that seam here, so no race decides the test.
    patch = "--- a/src/deep/inner.txt\n+++ b/src/deep/inner.txt\n@@ -1 +1 @@\n-clean\n+planted\n"
    write(tmp_path / "repo", "quality/bites/inner.patch", patch)
    write(tmp_path / "repo", "src/deep/inner.txt", "clean\n")
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "swapped", SWAPPED[kind]))
    before = fingerprint(root)
    checked = project_bite.owned

    def check_then_swap(copy: Path, *paths: str) -> object:
        verdict = checked(copy, *paths)
        (copy / swapped).rename(copy / "aside")
        (copy / swapped).symlink_to(root / swapped)
        return verdict

    monkeypatch.setattr(project_bite, "owned", check_then_swap)
    prove(root)
    assert fingerprint(root) == before
    out = capsys.readouterr().out
    assert "bite: lint: swapped: THE FAULT WAS NOT PLANTED\n" in out
    assert "is reached through a symbolic link; a fault changes the copy's own file" in out


def test_a_process_setup_left_swapping_a_directory_for_a_link_never_changes_the_real_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: the race the seam test fixes in place, run for real. The swapper ends by itself a
    # little after the proof would, and is killed by its pid once the proof is done.
    pidfile, swapper = tmp_path / "swapper.pid", tmp_path / "swap.sh"
    faults = [
        'write = { path = "src/w{n}.txt", content = "planted\\n" }',
        'replace = { path = "src/code.txt", old = "clean", new = "planted" }',
        'remove = ["src/code.txt"]',
    ]
    registry = (
        'setup = ["setsid -f sh '
        + str(swapper)
        + '"]\n'
        + "".join(bite("lint", f"f{n}", faults[n % 3].replace("{n}", str(n))) for n in range(24))
    )
    root = project(tmp_path / "repo", {"lint": "exit 0"}, registry)
    until = int(time.time()) + 20
    swapper.write_text(
        f"echo $$ > {pidfile}\n"
        f'while [ "$(date +%s)" -lt {until} ]; do\n'
        f"  [ -d src ] && [ ! -L src ] && mv src aside && ln -s {root / 'src'} src\n"
        "  sleep 0.001\n"
        "  [ -L src ] && rm src && mv aside src\n"
        "done\n",
        encoding="utf-8",
    )
    before = fingerprint(root)
    try:
        # Why: red either way, on a fault the gate never reads or on a copy the swapper left
        # in a state restoring refuses; what counts is the real tree.
        assert prove(root) == 1
    finally:
        if pidfile.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pidfile.read_text(encoding="utf-8")), signal.SIGKILL)
    assert fingerprint(root) == before
    assert "bite: red" in capsys.readouterr().out


def test_a_system_that_cannot_open_a_directory_without_following_a_link_plants_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_bite, "DESCRIPTORS", False)
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    before = fingerprint(root)
    assert prove(root) == 1
    assert capsys.readouterr().out == f"{project_bite.UNSUPPORTED}\nbite: red\n"
    assert fingerprint(root) == before


def test_a_copy_whose_name_now_names_another_directory_is_neither_read_nor_written(
    tmp_path: Path,
) -> None:
    copy, real = tmp_path / "copy", tmp_path / "real"
    copy.mkdir()
    real.mkdir()
    tree = project_bite.Tree.pin(copy)
    copy.rename(tmp_path / "aside")
    copy.mkdir()
    with pytest.raises(project_bite.Unprovable, match="no longer the directory the scratch copy"):
        tree.write("x.txt", b"planted\n")
    copy.rmdir()
    copy.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError, match=str(copy)):
        tree.write("x.txt", b"planted\n")
    assert list(real.iterdir()) == []


def test_a_patch_deletes_a_file_and_a_patch_that_makes_a_link_is_not_planted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    patches = {
        "delete": "--- a/src/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
        "link": "diff --git a/src/link b/src/link\nnew file mode 120000\n--- /dev/null\n"
        "+++ b/src/link\n@@ -0,0 +1 @@\n+code.txt\n\\ No newline at end of file\n",
    }
    for name, text in patches.items():
        write(tmp_path / "repo", f"quality/bites/{name}.patch", text)
    write(tmp_path / "repo", "src/old.txt", "old\n")
    gates = {"lint": 'test -f src/old.txt || { echo "L001 no old"; exit 1; }\ntest ! -e src/link'}
    registry = bite("lint", "deleted", 'patch = "quality/bites/delete.patch"') + bite(
        "lint", "a link", 'patch = "quality/bites/link.patch"'
    )
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root) == 1
    out = capsys.readouterr().out
    assert "bite: lint: deleted: bites\n" in out
    assert (
        "bite: lint: a link: THE FAULT WAS NOT PLANTED\n  src/link would be a symbolic link or a"
        " submodule, and a fault writes a file\n" in out
    )
