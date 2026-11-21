import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

import conformance
import gatelib
import owed
import project_bite
import runrecord
from tests import repos
from tests.repos import branch, commit, git, repository, write
from tests.servers import gone

TABLE = "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
LINT = 'if grep -rl planted src; then echo "L001 a planted fault"; exit 1; fi'
TESTS = 'ls tests/*.t >/dev/null 2>&1 || { echo "no test to run"; exit 1; }'
PLANT = 'write = { path = "src/_bite.txt", content = "planted\\n" }'
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


TIMING = re.compile(r" \(\d+\.\ds, lane \d+(; [^)]*)?\)")
AID = re.compile(r"^bite: (limit|load): .*\n", re.MULTILINE)


def shown(capsys: pytest.CaptureFixture[str]) -> str:
    return AID.sub("", TIMING.sub("", capsys.readouterr().out))


def prove(root: Path, *flags: str) -> int:
    return project_bite.main(["project_bite.py", "--root", str(root), "--jobs", "1", *flags])


def the_record(root: Path, path: Path) -> bool:
    return path in {root / gatelib.STATE, root / runrecord.TIMING}


def state(root: Path) -> tuple[list[str], str, list[str]]:
    paths = sorted(
        str(path)
        for path in root.rglob("*")
        if ".git" not in path.parts and not the_record(root, path)
    )
    status = git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
    tree = [line for line in status if line != f"?? {runrecord.TIMING}"]
    return tree, git(root, "rev-parse", "HEAD"), paths


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "scratch"
    directory.mkdir()
    repos.skeleton()
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
    out = shown(capsys).splitlines()
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
    out = shown(capsys)
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
    assert "bite: lint: a planted word: bites" in shown(capsys)


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
    out = shown(capsys)
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
    out = shown(capsys)
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
    assert shown(capsys).count(": bites\n") == 2


def test_a_replace_keeps_biting_after_an_unrelated_edit_breaks_the_same_patch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert shown(capsys).count(": bites\n") == 2
    write(root, "src/code.txt", lines.replace("line 2\n", "line 2\nan unrelated line\n"))
    commit(root, "feat(core): an unrelated line")
    assert prove(root) == 1
    out = shown(capsys)
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
    assert "bite: lint: two steps: bites" in shown(capsys)
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
    out = shown(capsys)
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
    assert shown(capsys).count(": bites\n") == 2


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
    assert shown(capsys).count(": bites\n") == 2


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
    out = shown(capsys)
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
    assert "bite: lint: a planted word: bites" in shown(capsys)


def test_a_setup_that_fails_is_red_and_plants_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = "setup = [\"sh -c 'echo no lockfile; exit 3'\"]\n" + bite("lint", "a planted word")
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    assert prove(root) == 1
    out = shown(capsys)
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
    assert "bite: refs: a planted word: bites" in shown(capsys)


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
    out = shown(capsys)
    assert out.count(": bites\n") == 3
    assert prove(root, "--jobs", "0") == 0


def test_ratchet_jobs_caps_the_lanes_as_it_caps_the_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gates = dict.fromkeys(("one", "two", "three"), LINT)
    registry = "".join(bite(name, "a planted word") for name in gates)
    root = project(tmp_path / "repo", gates, registry)
    seen: list[int] = []

    def record(
        _root: Path, _registry: object, _tasks: object, *, lanes: int, limit: float | None
    ) -> int:
        assert limit is None
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
    assert "gate 'unproven' (unproven) is bound to a tool and no [[bite]]" in shown(capsys)
    assert prove(root, "--gate", "lint") == 0
    assert "bite: green: 1 of 1 faults bit, over 1 gates" in shown(capsys)
    assert prove(root, "--gate", "unproven") == 1
    assert shown(capsys) == ("no bite to run: a gate with nothing to run is red\nbite: red\n")


def changed(root: Path) -> Path:
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
    assert shown(capsys) == (
        "bite: green: the change touches no path that defines a gate; nothing to prove\n"
    )
    assert list(scratch.iterdir()) == []


GATES = {"lint": LINT, "tests": LINT, "slow": "exit 1"}
GATE_ROWS = row("lint") + row("tests", "1") + row("slow", "2")
WORD = "a planted word"
BINDING = "docs/process/binding.md"
REGISTRY = "quality/bites.toml"
PATCH_FILE = "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1 +1 @@\n-clean\n+planted\n"
COMMANDS_MANIFEST = MANIFEST + (
    '[[group]]\nname = "gate-commands"\nreason = "What a gate runs."\n'
    'paths = ["justfile", "scripts/check.sh", "sub/justfile", "scripts/lint.sh"]\n'
)
JUSTFILE = (
    "lint:\n    sh gate lint\n"
    "tests: prepare\n    sh gate tests\n    just finish\n"
    "slow:\n    sh gate slow\n"
    "prepare:\n    echo prepare\n"
    "finish:\n    echo finish\n"
)
JUST_ROWS = GATE_ROWS.replace("`sh gate ", "`just ")
PIN = "- Ratchet plugin: 0.1.0 at " + "0" * 40
NOTHING = "bite: green: the change affects no gate of tiers 0 and 1; nothing to prove\n"


def every(why: str) -> str:
    return f"bite: fallback: {why}; proving every gate"


def registry_of(*extra: str) -> str:
    return "".join(bite(name, WORD) for name in GATES) + "".join(extra)


def targeted(
    tmp_path: Path,
    *,
    rows: str = GATE_ROWS,
    registry: str | None = None,
    files: dict[str, str] | None = None,
    inserts: str = "",
) -> Path:
    root = project(tmp_path / "repo", GATES, registry or registry_of(), rows=rows)
    if inserts:
        replaced(root, BINDING, "## Speed", f"{inserts}\n## Speed")
    write(root, "guards/manifest.toml", COMMANDS_MANIFEST)
    for path, text in {"lint.cfg": "x\n", "scripts/check.sh": "x\n", **(files or {})}.items():
        write(root, path, text)
    commit(root, "feat(core): a manifest")
    branch(root)
    return root


def with_reads(bullets: str) -> str:
    return f"## What each gate reads\n\n{bullets}"


def replaced(root: Path, path: str, *edit: str) -> None:
    old, new = edit
    text = (root / path).read_text(encoding="utf-8")
    assert old in text
    write(root, path, text.replace(old, new, 1))


def proved(out: list[str]) -> list[str]:
    return [line.split(":")[1].strip() for line in out if line.endswith(": bites")]


def test_a_bite_added_or_changed_proves_its_gate_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    replaced(root, REGISTRY, 'expect = "L001"', 'expect = "L001 a planted"')
    write(root, REGISTRY, (root / REGISTRY).read_text() + bite("tests", "another"))
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[0] == f"bite: a gate may have changed ({REGISTRY})"
    assert out[1:3] == [f"bite: lint: bite `{WORD}` changed", "bite: tests: bite `another` added"]
    assert proved(out) == ["lint", "tests", "tests"]


def test_a_bite_that_moves_to_another_gate_counts_under_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, registry=registry_of(bite("tests", "second")))
    replaced(root, REGISTRY, 'gate = "tests"\nname = "second"', 'gate = "lint"\nname = "second"')
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1:3] == ["bite: lint: bite `second` added", "bite: tests: bite `second` removed"]
    assert proved(out) == ["lint", "lint", "tests"]


def test_a_removed_bite_names_its_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = targeted(tmp_path, registry=registry_of(bite("tests", "second")))
    write(root, REGISTRY, registry_of())
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == "bite: tests: bite `second` removed"
    assert proved(out) == ["tests"]


def test_a_change_to_setup_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    write(root, REGISTRY, 'setup = ["true"]\n\n' + (root / REGISTRY).read_text())
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every(f"{REGISTRY} changed `setup` or `include`, which every copy holds")
    assert proved(out) == ["lint", "tests"]


def patched(tmp_path: Path) -> Path:
    named = bite("tests", "by patch", 'patch = "quality/bites/p.patch"')
    registry = bite("lint", WORD) + named + bite("slow", WORD)
    return targeted(tmp_path, registry=registry, files={"quality/bites/p.patch": PATCH_FILE})


def test_a_patch_a_bite_names_proves_that_bites_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = patched(tmp_path)
    write(root, "quality/bites/p.patch", PATCH_FILE.replace("+planted", "+planted again"))
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[0] == "bite: a gate may have changed (quality/bites/p.patch)"
    assert out[1] == "bite: tests: patch quality/bites/p.patch is named by bite `by patch`"
    assert proved(out) == ["tests"]


def test_a_file_under_the_bites_directory_no_bite_names_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = patched(tmp_path)
    write(root, "quality/bites/helper.sh", "echo setup helper\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    why = "quality/bites/helper.sh is under quality/bites/ and no bite names it as its patch, so"
    assert out[1] == every(f"{why} setup may run it")
    assert proved(out) == ["lint", "tests"]


def test_a_binding_row_that_changed_proves_its_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    replaced(root, BINDING, "| tests | a tool | 1 |", "| tests | a tool | 0 |")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == "bite: tests: the binding's row `tests` changed"
    assert proved(out) == ["tests"]


SUITES = (
    "## Tests\n\n| Suite | Gate | Files | Run files |\n| --- | --- | --- | --- |\n"
    "| unit | `sh gate tests` | `tests/**` | `just test-files` |\n"
    "| slow | `sh gate slow` | `slow/**` | `none: it boots the whole service` |\n"
)


def suited(tmp_path: Path) -> Path:
    gates = {**GATES, "tests-changed": LINT}
    rows = GATE_ROWS + row("tests-changed", "1")
    registry = registry_of(bite("tests-changed", WORD))
    root = project(tmp_path / "repo", gates, registry, rows=rows)
    replaced(root, BINDING, "## Speed", f"{SUITES}\n## Speed")
    write(root, "guards/manifest.toml", COMMANDS_MANIFEST)
    write(root, "lint.cfg", "x\n")
    commit(root, "feat(core): a manifest")
    branch(root)
    return root


def test_a_changed_suites_row_proves_tests_changed_and_no_other_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = suited(tmp_path)
    replaced(
        root,
        BINDING,
        "| unit | `sh gate tests` | `tests/**` |",
        "| unit | `sh gate tests` | `t/**` |",
    )
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == "bite: tests-changed: the binding's suites row `unit` changed"
    assert proved(out) == ["tests-changed"]


def test_an_added_or_removed_suites_row_names_the_gate_once_for_each_row(
    tmp_path: Path,
) -> None:
    root = suited(tmp_path)
    replaced(
        root,
        BINDING,
        "| slow | `sh gate slow` | `slow/**` | `none: it boots the whole service` |\n",
        "",
    )
    replaced(
        root, BINDING, "\n## Speed", "| big | `sh gate slow` | `big/**` | `just big` |\n\n## Speed"
    )
    base = gatelib.base_commit(root)
    assert project_bite.binding_causes(root, base) == [
        project_bite.Cause("tests-changed", "the binding's suites row `big` changed"),
        project_bite.Cause("tests-changed", "the binding's suites row `slow` changed"),
    ]


def test_a_padded_suites_table_changes_no_row(tmp_path: Path) -> None:
    root = suited(tmp_path)
    replaced(root, BINDING, "| --- | --- | --- | --- |", "| ----- | ----- | ----- | ----- |")
    replaced(root, BINDING, "| unit |", "| unit    |")
    assert project_bite.binding_causes(root, gatelib.base_commit(root)) == []


def test_a_binding_change_beside_the_suites_table_still_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = suited(tmp_path)
    replaced(root, BINDING, "## Tests\n", "## Tests\n\nA word more.\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every(f"{BINDING} changed outside its gate rows, which a gate may read")


def test_a_binding_without_a_suites_table_has_no_suites_cause(tmp_path: Path) -> None:
    root = targeted(tmp_path)
    replaced(root, BINDING, "| tests | a tool | 1 |", "| tests | a tool | 0 |")
    causes = project_bite.binding_causes(root, gatelib.base_commit(root))
    assert {c.gate for c in causes} == {"tests"}


def test_a_binding_change_outside_the_rows_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    replaced(root, BINDING, "| a | b |", "| a | c |")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every(f"{BINDING} changed outside its gate rows, which a gate may read")
    assert proved(out) == ["lint", "tests"]


TREE = " tree " + "1" * 40


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (PIN, PIN.replace("0.1.0", "0.2.0")),
        (PIN + TREE, PIN.replace("0.1.0", "0.2.0") + TREE),
        (PIN, PIN + TREE),
        (PIN + TREE, PIN + TREE.replace("1", "2")),
        (PIN + TREE, ""),
    ],
    ids=["version", "tree form, version", "tree added", "tree changed", "line removed"],
)
def test_a_change_to_the_plugin_pin_line_alone_proves_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, old: str, new: str
) -> None:
    root = targeted(tmp_path, inserts=f"{old}\n")
    replaced(root, BINDING, old, new)
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every("the plugin pin changed, and the plugin supplies gates")
    assert proved(out) == ["lint", "tests"]


@pytest.mark.parametrize(
    "path", ["scripts/check.sh", "lint.cfg"], ids=["gate-commands", "tool-config"]
)
def test_a_path_that_names_no_gate_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    verb = "runs" if path.startswith("scripts") else "reads"
    why = f"{path} changed, and no record says which gate {verb} it"
    root = targeted(tmp_path)
    write(root, path, "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every(why)
    assert proved(out) == ["lint", "tests"]


def test_a_path_in_tool_config_and_fitness_goes_to_every_gate_whatever_the_bullets_say(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fitness = '[[group]]\nname = "fitness"\nreason = "A fitness test."\npaths = ["lint.cfg"]\n'
    files = {"guards/manifest.toml": COMMANDS_MANIFEST + fitness}
    root = targeted(
        tmp_path, inserts=with_reads("- lint: lint.cfg\n- tests: src/**\n"), files=files
    )
    write(root, "lint.cfg", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert proved(out) == ["lint", "tests"]
    assert out[1].startswith("bite: fallback: lint.cfg")
    assert out[1].endswith("proving every gate")


def test_a_path_no_row_names_proves_the_gates_whose_bullet_reads_it_and_those_with_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, inserts=with_reads("- lint: lint.cfg\n"))
    write(root, "lint.cfg", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1:3] == [
        "bite: lint: lint.cfg changed, and `lint` reads it",
        "bite: tests: lint.cfg changed, and `tests` has no bullet under ## What each gate reads",
    ]
    assert proved(out) == ["lint", "tests"]


def test_a_gate_whose_bullet_names_none_of_the_changed_paths_is_left_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reads = "- lint: lint.cfg\n- tests: src/**, docs\n"
    root = targeted(tmp_path, inserts=with_reads(reads))
    write(root, "lint.cfg", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == "bite: lint: lint.cfg changed, and `lint` reads it"
    assert proved(out) == ["lint"]
    assert not any(line.startswith("bite: fallback") for line in out)


def test_a_path_that_is_not_tool_config_ignores_the_bullets_and_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, inserts=with_reads("- lint: scripts/lint.sh\n- tests: src/**\n"))
    write(root, "scripts/check.sh", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    why = "scripts/check.sh changed, and no record says which gate runs it"
    assert out[1] == every(why)
    assert proved(out) == ["lint", "tests"]


def test_a_question_in_two_rows_sends_a_binding_change_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    twin = row("lint", "1", tool="none").replace("`sh gate lint`", "`sh gate other`")
    root = targeted(tmp_path, rows=GATE_ROWS + twin)
    replaced(root, BINDING, "| tests | a tool | 1 |", "| tests | a tool | 0 |")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    why = f"{BINDING} has two gate rows for one question, so one could hide behind the other"
    assert out[1] == every(why)
    assert proved(out) == ["lint", "tests"]


def test_a_changed_row_that_names_no_gate_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, rows=GATE_ROWS + row("extra", tool="none"))
    replaced(root, BINDING, "`sh gate extra`", "")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every("the binding's row `extra` changed and names no gate")
    assert proved(out) == ["lint", "tests"]


def test_a_change_that_affects_only_a_tier_2_gate_says_so_and_proves_nothing(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    replaced(root, BINDING, "| slow | a tool | 2 |", "| slow | a tool | 3 |")
    assert prove(root, "--changed") == 0
    out = shown(capsys)
    assert out.endswith(NOTHING)
    assert "touches no path" not in out
    assert list(scratch.iterdir()) == []


LINES = "first\nsecond\nthird\nfourth\nfifth\nsixth\n"
SECOND = (
    "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1,3 +1,3 @@\n first\n-second\n+planted\n third\n"
)
BROKEN = (
    "bite 'deep fault' of gate 'slow' cannot be planted: quality/bites/p.patch no longer applies:"
    " error: patch failed: src/code.txt:1; error: src/code.txt: patch does not apply; make the"
    " patch again against the file as it is now"
)


def deep(tmp_path: Path, fault: str, *, setup: str = "") -> Path:
    registry = setup + bite("lint", WORD) + bite("tests", WORD) + bite("slow", "deep fault", fault)
    files = {"quality/bites/p.patch": SECOND, "src/code.txt": LINES, "bench/one.b": "a\n"}
    return targeted(tmp_path, registry=registry, files=files)


@pytest.mark.parametrize(
    "case",
    [
        ('patch = "quality/bites/p.patch"', ("first\n", "renamed\n"), BROKEN),
        (
            'replace = { path = "src/code.txt", old = "second", new = "planted" }',
            ("second\n", "2nd\n"),
            (
                "bite 'deep fault' of gate 'slow' cannot be planted: src/code.txt no longer holds"
                " the text the fault replaces; write 'old' as the file has it now: 'second'"
            ),
        ),
        (
            'remove = ["bench/**"]',
            None,
            (
                "bite 'deep fault' of gate 'slow' cannot be planted: no file git sees matches"
                " bench/**; write the globs to match the collection as it is now"
            ),
        ),
    ],
    ids=["a patch whose context line was renamed", "a replace whose text is gone", "a remove"],
)
def test_a_change_that_breaks_a_tier_2_fault_is_red_before_any_gate_runs(
    tmp_path: Path,
    scratch: Path,
    *,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, tuple[str, str] | None, str],
) -> None:
    fault, edit, says = case
    root = deep(tmp_path, fault)
    assert project_bite.failures(root) == []
    if edit is None:
        (root / "bench/one.b").unlink()
    else:
        replaced(root, "src/code.txt", *edit)
    commit(root, "feat(core): an edit the fault leans on")
    assert project_bite.failures(root) == [f"{REGISTRY}: {says}"]
    assert [str(found) for found in conformance.check_bites(root)] == [f"{REGISTRY}: {says}"]
    assert prove(root, "--changed") == 1
    assert f"{REGISTRY}: {says}" in shown(capsys)
    assert list(scratch.iterdir()) == []


def test_the_check_reads_the_work_tree_as_git_sees_it(tmp_path: Path) -> None:
    root = deep(tmp_path, 'patch = "quality/bites/p.patch"')
    replaced(root, "src/code.txt", "first\n", "renamed\n")
    assert project_bite.failures(root) == [f"{REGISTRY}: {BROKEN}"]
    write(root, "quality/bites/p.patch", SECOND.replace(" first\n", " renamed\n"))
    assert project_bite.failures(root) == []
    git(root, "checkout", "-q", "--", ".")
    write(root, "src/new.txt", "planted here\n")
    swap = 'replace = { path = "src/new.txt", old = "here", new = "now" }'
    write(root, REGISTRY, bite("lint", WORD) + bite("tests", WORD) + bite("slow", "new", swap))
    assert project_bite.failures(root) == []
    write(root, ".gitignore", "ignored/\n*.secret\nsrc/new.txt\n")
    assert project_bite.failures(root) == [
        (
            f"{REGISTRY}: bite 'new' of gate 'slow' cannot be planted: src/new.txt is not a file"
            " git sees, and a fault plants only into what git sees; if the file moved, point"
            " 'path' at its new name, or else remove the bite and add one that plants"
        )
    ]


def test_a_fault_on_a_file_setup_makes_is_not_checked_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    swap = 'replace = { path = "src/made.txt", old = "made", new = "planted" }'
    root = deep(tmp_path, swap, setup="setup = [\"sh -c 'echo made > src/made.txt'\"]\n")
    assert project_bite.failures(root) == []
    replaced(root, "src/code.txt", "sixth\n", "sixth\nseventh\n")
    assert prove(root, "--changed") == 0
    assert shown(capsys).startswith(
        "bite: not checked: bite 'deep fault' of gate 'slow': nothing git sees is at src/made.txt,"
        " and the registry's `setup` may make it in the scratch copy; the full proof plants it\n"
    )
    replaced(root, REGISTRY, "setup = [\"sh -c 'echo made > src/made.txt'\"]\n", "")
    assert project_bite.failures(root) == [
        (
            f"{REGISTRY}: bite 'deep fault' of gate 'slow' cannot be planted: src/made.txt is not"
            " a file git sees, and a fault plants only into what git sees; if the file moved,"
            " point 'path' at its new name, or else remove the bite and add one that plants"
        )
    ]


SETUP = "setup = [\"sh -c 'echo made > src/made.txt'\"]\n"
MADE = "--- a/src/made.txt\n+++ b/src/made.txt\n@@ -1 +1 @@\n-made\n+planted\n"
NOT_CHECKED = (
    "bite 'deep fault' of gate 'slow': nothing git sees is at src/made.txt, and the registry's"
    " `setup` may make it in the scratch copy; the full proof plants it"
)


@pytest.mark.parametrize(
    "fault",
    [
        ('patch = "quality/bites/p.patch"', BROKEN),
        (
            (
                'replace = [{ path = "src/made.txt", old = "made", new = "planted" },'
                ' { path = "src/code.txt", old = "first", new = "planted" },'
                ' { path = "src/made.txt", old = "planted", new = "made" }]'
            ),
            (
                "bite 'deep fault' of gate 'slow' cannot be planted: src/code.txt no longer holds"
                " the text the fault replaces; write 'old' as the file has it now: 'first'"
            ),
        ),
    ],
    ids=["a patch", "a replace"],
)
def test_with_setup_every_part_of_a_fault_that_git_sees_is_still_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fault: tuple[str, str]
) -> None:
    text, says = fault
    root = deep(tmp_path, text, setup=SETUP)
    write(root, "quality/bites/p.patch", SECOND + MADE)
    commit(root, "test(bite): the fault reaches a file setup makes")
    assert project_bite.failures(root) == []
    conformance.run(root)
    assert f"not checked: {REGISTRY}: {NOT_CHECKED}\n" in capsys.readouterr().out
    replaced(root, "src/code.txt", "first\n", "renamed\n")
    commit(root, "feat(core): an edit the fault leans on")
    assert project_bite.failures(root) == [f"{REGISTRY}: {says}"]


@pytest.mark.parametrize(
    ("fault", "builds"),
    [
        ('patch = "quality/bites/p.patch"', 1),
        ('replace = { path = "src/code.txt", old = "second", new = "planted" }', 0),
    ],
    ids=["a patch fault", "no patch fault"],
)
def test_one_run_reads_the_tree_as_git_sees_it_at_most_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    builds: int,
) -> None:
    built: list[Path] = []
    sees = project_bite.as_git_sees

    @contextmanager
    def counted(root: Path) -> Iterator[dict[str, str]]:
        built.append(root)
        with sees(root) as store:
            yield store

    monkeypatch.setattr(project_bite, "as_git_sees", counted)
    root = deep(tmp_path, fault)
    conformance.run(root)
    assert len(built) == builds, f"one conformance run built {len(built)} scratch indexes"
    built.clear()
    replaced(root, "src/code.txt", "sixth\n", "sixth\nseventh\n")
    assert prove(root, "--changed") == 0
    assert len(built) == builds, f"one bite gate run built {len(built)} scratch indexes"
    capsys.readouterr()


MOVED = "if the file moved, {}, or else remove the bite and add one that plants"


@pytest.mark.parametrize("setup", ["", SETUP], ids=["no setup", "a setup"])
@pytest.mark.parametrize(
    "fault",
    [
        (
            'patch = "quality/bites/p.patch"',
            "quality/bites/p.patch changes src/code.txt, which git does not see; "
            + MOVED.format("make the patch again against its new name"),
        ),
        (
            'replace = { path = "src/code.txt", old = "second", new = "planted" }',
            "src/code.txt is not a file git sees, and a fault plants only into what git sees; "
            + MOVED.format("point 'path' at its new name"),
        ),
        (
            'remove = ["bench/**"]',
            (
                "no file git sees matches bench/**; write the globs to match the collection as it"
                " is now"
            ),
        ),
    ],
    ids=["a patch", "a replace", "a remove"],
)
def test_a_change_that_moves_what_a_fault_reaches_is_red_and_says_what_to_do(
    tmp_path: Path, setup: str, fault: tuple[str, str]
) -> None:
    text, says = fault
    root = deep(tmp_path, text, setup=setup)
    git(root, "mv", "src/code.txt", "src/moved.txt")
    git(root, "mv", "bench", "benchmarks")
    commit(root, "refactor(core): move the code and the benchmarks")
    prefix = "bite 'deep fault' of gate 'slow' cannot be planted"
    assert project_bite.failures(root) == [f"{REGISTRY}: {prefix}: {says}"]


def test_a_patch_on_a_file_git_ignores_is_red_as_its_plant_is(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fault = 'patch = "quality/bites/p.patch"'
    registry = bite("lint", "on an ignored file", fault) + bite("tests", WORD) + bite("slow", WORD)
    patch = SECOND.replace("src/code.txt", "src/made.secret")
    files = {"quality/bites/p.patch": patch, "src/made.secret": LINES}
    root = targeted(tmp_path, registry=registry, files=files)
    assert (root / "src/made.secret").is_file()
    assert project_bite.failures(root) == [
        f"{REGISTRY}: bite 'on an ignored file' of gate 'lint' cannot be planted: "
        "quality/bites/p.patch changes src/made.secret, which git does not see; "
        + MOVED.format("make the patch again against its new name")
    ]
    assert prove(root, "--gate", "lint") == 1
    assert "on an ignored file: THE FAULT WAS NOT PLANTED" in shown(capsys)
    assert list(scratch.iterdir()) == []


def test_a_change_that_reaches_a_tier_2_fault_says_its_plant_was_checked(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = deep(tmp_path, 'patch = "quality/bites/p.patch"')
    replaced(root, "src/code.txt", "sixth\n", "sixth\nseventh\n")
    assert prove(root, "--changed") == 0
    assert shown(capsys) == (
        "bite: a gate may have changed (src/code.txt)\n"
        "bite: slow: src/code.txt is changed by bite `deep fault`; a gate of tier 2, which a pull"
        " request does not prove, and each of its patch, replace and remove faults was checked"
        " to still plant\n" + NOTHING
    )
    assert list(scratch.iterdir()) == []


def test_a_changed_file_a_write_fault_plants_proves_that_bites_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plant = 'write = { path = "src/t.txt", content = "planted\\n" }'
    registry = bite("lint", WORD) + bite("tests", "w", plant) + bite("slow", WORD)
    root = targeted(tmp_path, registry=registry, files={"src/t.txt": "one\n"})
    write(root, "src/t.txt", "two\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[0] == "bite: a gate may have changed (src/t.txt)"
    assert out[1] == "bite: tests: src/t.txt is changed by bite `w`"
    assert proved(out) == ["tests"]


def test_a_path_in_two_groups_takes_the_wider_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wide = COMMANDS_MANIFEST.replace('"scripts/lint.sh"]', '"scripts/lint.sh", "lint.cfg"]')
    inserts = with_reads("- lint: lint.cfg\n- tests: src/**\n")
    root = targeted(tmp_path, inserts=inserts, files={"guards/manifest.toml": wide})
    write(root, "lint.cfg", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every("lint.cfg changed, and no record says which gate runs it")
    assert proved(out) == ["lint", "tests"]


def test_a_bullet_that_wraps_onto_a_second_line_reads_both_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reads = with_reads("- lint: other.cfg,\n  lint.cfg\n- tests: src/**\n")
    root = targeted(tmp_path, inserts=reads)
    write(root, "lint.cfg", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == "bite: lint: lint.cfg changed, and `lint` reads it"
    assert proved(out) == ["lint"]


def test_a_change_that_names_only_gates_with_no_bite_is_green_and_says_so(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    waiting = row("later", tool="later: code:listener")
    root = targeted(tmp_path, rows=GATE_ROWS + waiting)
    replaced(
        root,
        BINDING,
        "| later | later: code:listener | 0 |",
        "| later | later: code:listener | 1 |",
    )
    assert prove(root, "--changed") == 0
    assert shown(capsys).endswith(
        "bite: green: the gates the change affects have no bite; nothing to prove\n"
    )
    assert list(scratch.iterdir()) == []


def test_a_change_to_only_a_shipped_row_is_green_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = "`ratchet` plugin `scripts/markers.py`"
    root = targeted(tmp_path, rows=GATE_ROWS + row("markers", tool=tool))
    replaced(root, BINDING, f"| markers | {tool} | 0 |", f"| markers | {tool} | 1 |")
    assert prove(root, "--changed") == 0
    assert shown(capsys).endswith(
        "bite: green: the gates the change affects have no bite; nothing to prove\n"
    )


def test_a_gate_chosen_outside_the_affected_set_is_green_under_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    replaced(root, BINDING, "| tests | a tool | 1 |", "| tests | a tool | 0 |")
    assert prove(root, "--changed", "--gate", "lint") == 0
    assert shown(capsys).endswith(
        "bite: green: the gates the change affects have no bite; nothing to prove\n"
    )


def test_a_change_to_the_justfile_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, rows=JUST_ROWS, files={"justfile": JUSTFILE})
    replaced(root, "justfile", "echo prepare", "echo prepared")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[0] == "bite: a gate may have changed (justfile)"
    assert out[1] == every("the justfile changed, and its recipes are code the proof cannot read")
    assert proved(out) == ["lint", "tests"]


def test_a_justfile_in_two_gate_groups_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fitness = '[[group]]\nname = "fitness"\nreason = "A fitness test."\npaths = ["justfile"]\n'
    files = {"justfile": JUSTFILE, "guards/manifest.toml": COMMANDS_MANIFEST + fitness}
    root = targeted(tmp_path, rows=JUST_ROWS, files=files)
    replaced(root, "justfile", "echo finish", "echo finished")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every("the justfile changed, and its recipes are code the proof cannot read")
    assert proved(out) == ["lint", "tests"]


def test_a_file_a_patch_fault_edits_proves_that_bites_gate(tmp_path: Path) -> None:
    root = patched(tmp_path)
    targets = project_bite.fault_targets(root, "main")
    assert project_bite.targeted_by(targets, "src/code.txt") == {("tests", "by patch")}


def test_a_patch_fault_that_edits_a_file_with_a_space_in_its_name_is_targeted(
    tmp_path: Path,
) -> None:
    patch = PATCH_FILE + PATCH_FILE.replace("src/code.txt", "src/my code.txt")
    named = bite("tests", "by patch", 'patch = "quality/bites/p.patch"')
    registry = bite("lint", WORD) + named + bite("slow", WORD)
    files = {"quality/bites/p.patch": patch, "src/my code.txt": "clean\n"}
    root = targeted(tmp_path, registry=registry, files=files)
    targets = project_bite.fault_targets(root, "main")
    assert project_bite.targeted_by(targets, "src/my code.txt") == {("tests", "by patch")}


def patch_targets(tmp_path: Path, patch: str) -> project_bite.Targets:
    named = bite("tests", "by patch", 'patch = "quality/bites/p.patch"')
    registry = bite("lint", WORD) + named + bite("slow", WORD)
    root = targeted(tmp_path, registry=registry, files={"quality/bites/p.patch": patch})
    return project_bite.fault_targets(root, "main")


def named_by(targets: project_bite.Targets, *paths: str) -> None:
    for path in paths:
        assert project_bite.targeted_by(targets, path) == {("tests", "by patch")}, path


def test_a_patch_fault_that_renames_a_file_is_targeted_by_both_names(tmp_path: Path) -> None:
    patch = (
        "diff --git a/old name.txt b/new name.txt\nsimilarity index 100%\n"
        "rename from old name.txt\nrename to new name.txt\n"
    )
    targets = patch_targets(tmp_path, patch)
    named_by(targets, "old name.txt", "new name.txt")
    assert project_bite.targeted_by(targets, "other.txt") == set()


def test_a_patch_fault_that_edits_a_file_with_a_non_ascii_name_is_targeted(
    tmp_path: Path,
) -> None:
    quoted = '"%s/src/caf\\303\\251.txt"'
    patch = PATCH_FILE.replace("a/src/code.txt", quoted % "a").replace(
        "b/src/code.txt", quoted % "b"
    )
    named_by(patch_targets(tmp_path, patch), "src/caf\u00e9.txt")


def test_a_patch_fault_that_only_changes_a_mode_is_targeted(tmp_path: Path) -> None:
    patch = "diff --git a/src/code.txt b/src/code.txt\nold mode 100644\nnew mode 100755\n"
    named_by(patch_targets(tmp_path, patch), "src/code.txt")


def test_a_patch_fault_written_with_mnemonic_prefixes_is_targeted(tmp_path: Path) -> None:
    patch = PATCH_FILE.replace("--- a/", "--- i/").replace("+++ b/", "+++ w/")
    named_by(patch_targets(tmp_path, patch), "src/code.txt")


def test_a_fault_target_with_glob_characters_in_its_name_is_matched_literally(
    tmp_path: Path,
) -> None:
    fault = 'replace = { path = "src/[id].txt", old = "clean", new = "planted" }'
    registry = bite("lint", WORD, fault) + bite("tests", WORD) + bite("slow", WORD)
    root = targeted(tmp_path, registry=registry, files={"src/[id].txt": "clean\n"})
    targets = project_bite.fault_targets(root, "main")
    assert project_bite.targeted_by(targets, "src/[id].txt") == {("lint", WORD)}
    assert project_bite.targeted_by(targets, "src/i.txt") == set()


@pytest.mark.parametrize("path", [".gitignore", "src/.gitattributes"])
def test_a_change_to_an_ignore_file_at_any_depth_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    root = targeted(tmp_path)
    write(root, path, "ignored/\n*.secret\n" + "build/\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every(
        f"{path} changed, and the proof reads the ignore rules when it plants a fault"
    )
    assert proved(out) == ["lint", "tests"]


def test_a_patch_git_cannot_read_adds_its_gate_to_any_change(tmp_path: Path) -> None:
    named = bite("tests", "by patch", 'patch = "quality/bites/p.patch"')
    registry = bite("lint", WORD) + named + bite("slow", WORD)
    root = targeted(tmp_path, registry=registry, files={"quality/bites/p.patch": "garbage\n"})
    targets = project_bite.fault_targets(root, "main")
    assert project_bite.targeted_by(targets, "anything/at/all.txt") == {("tests", "by patch")}


def test_a_tool_config_path_no_bullet_names_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, inserts=with_reads("- lint: src/**\n- tests: src/**\n"))
    write(root, "lint.cfg", "changed\n")
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every("lint.cfg changed, and no bullet names it")
    assert proved(out) == ["lint", "tests"]


def test_a_file_a_replace_fault_edits_proves_that_bites_gate(tmp_path: Path) -> None:
    swapped = bite(
        "tests", "swapped", 'replace = { path = "src/code.txt", old = "clean", new = "planted" }'
    )
    root = targeted(tmp_path, registry=bite("lint", WORD) + swapped + bite("slow", WORD))
    targets = project_bite.fault_targets(root, "main")
    assert project_bite.targeted_by(targets, "src/code.txt") == {("tests", "swapped")}


def test_a_registry_added_by_the_change_goes_to_every_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", GATES, None, rows=GATE_ROWS)
    write(root, "guards/manifest.toml", COMMANDS_MANIFEST)
    commit(root, "feat(core): a manifest")
    branch(root)
    write(root, REGISTRY, registry_of())
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[1] == every(f"{REGISTRY} was added, removed or does not parse")
    assert proved(out) == ["lint", "tests"]


def test_a_patch_fault_whose_file_is_absent_names_no_path(tmp_path: Path) -> None:
    named = bite("tests", "by patch", 'patch = "quality/bites/missing.patch"')
    root = targeted(tmp_path, registry=bite("lint", WORD) + named + bite("slow", WORD))
    assert (
        project_bite.targeted_by(project_bite.fault_targets(root, "main"), "src/code.txt") == set()
    )


def test_a_removed_file_a_remove_fault_covers_proves_that_bites_gate(tmp_path: Path) -> None:
    emptied = bite("tests", "empty", 'remove = ["tests/*.t"]', expect="no test")
    root = targeted(tmp_path, registry=bite("lint", WORD) + emptied + bite("slow", WORD))
    (root / "tests/one.t").unlink()
    targets = project_bite.fault_targets(root, "main")
    assert project_bite.targeted_by(targets, "tests/one.t") == {("tests", "empty")}
    touched = project_bite.gate_paths_changed(root, "main")
    assert [t.path for t in touched or []] == ["tests/one.t"]


def test_a_gate_dropped_by_gate_does_not_hide_a_registry_gap_under_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path, registry=bite("lint", WORD) + bite("slow", WORD))
    replaced(root, BINDING, "| tests | a tool | 1 |", "| tests | a tool | 0 |")
    assert prove(root, "--changed", "--gate", "lint") == 1
    assert "gate 'tests' (tests) is bound to a tool and no [[bite]]" in shown(capsys)


def owe_what_conformance_finds(root: Path, *, keep: Callable[[str], bool]) -> None:
    bites = conformance.rule_of(conformance.check_bites)
    keys = [replace(found, rule=bites).key() for found in conformance.check_bites(root)]
    entries = [
        owed.Entry(path, rule, problem, "0.13.1") for rule, path, problem in keys if keep(problem)
    ]
    write(root, str(owed.LEDGER), owed.render(entries))


def upgraded(tmp_path: Path) -> Path:
    root = targeted(tmp_path, registry=bite("lint", WORD))
    replaced(root, BINDING, "| lint | a tool | 0 |", "| lint | a newer tool | 0 |")
    return root


TESTS_GAP = "gate 'tests' (tests) is bound to a tool and no [[bite]] plants a fault for it"
SLOW_GAP = "gate 'slow' (slow) is bound to a tool and no [[bite]] plants a fault for it"


def test_a_gap_an_upgrade_owes_is_excused_named_and_the_rest_is_proven(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = upgraded(tmp_path)
    owe_what_conformance_finds(root, keep=lambda _: True)
    assert prove(root, "--changed") == 0
    out = shown(capsys).splitlines()
    assert out[:3] == [
        f"bite: owed: {REGISTRY}: {TESTS_GAP}",
        f"bite: owed: {REGISTRY}: {SLOW_GAP}",
        f"bite: 2 gaps owed since 0.13.1, listed in {owed.LEDGER}",
    ]
    assert proved(out) == ["lint"]
    assert out[-1].startswith("bite: green: 1 of 1 faults bit")


def test_a_gap_no_entry_owes_is_still_red_beside_one_that_is_owed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = upgraded(tmp_path)
    owe_what_conformance_finds(root, keep=lambda problem: problem == TESTS_GAP)
    assert prove(root, "--changed") == 1
    assert shown(capsys).splitlines() == [
        f"bite: owed: {REGISTRY}: {TESTS_GAP}",
        f"bite: 1 gap owed since 0.13.1, listed in {owed.LEDGER}",
        f"{REGISTRY}: {SLOW_GAP}",
        "bite: red",
    ]


def test_a_ledger_that_does_not_parse_excuses_nothing_and_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = upgraded(tmp_path)
    write(root, str(owed.LEDGER), "[[owed]\n")
    assert prove(root, "--changed") == 1
    out = shown(capsys).splitlines()
    assert out[0].startswith(f"{owed.LEDGER}: does not parse")
    assert out[1:] == [f"{REGISTRY}: {TESTS_GAP}", f"{REGISTRY}: {SLOW_GAP}", "bite: red"]


def test_a_ledger_that_does_not_parse_is_not_red_in_a_bite_gate_with_nothing_to_excuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    write(root, str(owed.LEDGER), "[[owed]\n")
    assert prove(root, "--gate", "lint") == 0
    assert "does not parse" not in shown(capsys)


def test_a_pull_request_with_nothing_to_compare_with_proves_the_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", WORD))
    assert prove(root, "--changed") == 0
    out = shown(capsys)
    assert "bite: a gate may have changed (no merge base)\n" in out
    assert every("there is no merge base to compare with") + "\n" in out


def test_the_flag_proves_every_gate_of_tiers_0_and_1_on_a_branch_with_no_gate_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    write(root, "src/more.txt", "product code\n")
    assert prove(root, "--pull-request-gates") == 0
    out = shown(capsys).splitlines()
    assert proved(out) == ["lint", "tests"]
    assert not any(line.startswith("bite: fallback") for line in out)


def test_the_flag_and_changed_together_are_refused(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = targeted(tmp_path)
    with pytest.raises(SystemExit) as stop:
        prove(root, "--changed", "--pull-request-gates")
    assert stop.value.code == 2
    assert "--changed and --pull-request-gates" in capsys.readouterr().err
    assert list(scratch.iterdir()) == []


def test_a_bite_of_the_gate_that_runs_the_bites_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    monkeypatch.setenv(project_bite.BITING, "1")
    assert prove(root) == 1
    assert shown(capsys) == ("a bite cannot plant a fault for the gate that runs it\nbite: red\n")


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
    assert "bite: env: a planted word: bites" in shown(capsys)


def lanes() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name.startswith("bite-lane")]


@pytest.mark.parametrize("receiver", ["the main thread", "a lane's thread"])
def test_an_interrupt_stops_the_gates_promptly_whichever_thread_receives_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], receiver: str
) -> None:
    started, stopped = tmp_path / "started", tmp_path / "stopped"
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
    out = shown(capsys)
    assert out.endswith("bite: red: interrupted by signal 15\n")
    assert "bite: next" not in out
    assert stopped.read_text(encoding="utf-8") == "ended by the runner\n"
    assert lanes() == []
    assert list((tmp_path / "scratch").iterdir()) == []


def test_after_an_interrupt_the_gate_it_stopped_runs_no_other_bite_and_prints_no_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started = tmp_path / "started"
    registry = "".join(bite("slow", f"bite {n}") for n in range(1000))
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
    assert shown(capsys) == "bite: red: interrupted by signal 15\n"


def test_an_interrupt_while_the_lanes_start_waits_for_every_lane_before_the_copies_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert shown(capsys).endswith("bite: red: interrupted by signal 15\n")
    assert lanes() == []
    assert list((tmp_path / "scratch").iterdir()) == []


def test_what_a_gate_leaves_running_ends_with_it_and_cannot_hold_the_run_open(
    tmp_path: Path,
) -> None:
    forked = tmp_path / "forked"
    ran = project_bite.Shell().run(
        ["sh", "-c", f"sleep 60 & echo $! > {forked}; echo done"], tmp_path
    )
    assert ran[:2] == (0, f"done\nbite: {gatelib.LEFT_RUNNING}\n")
    assert gone(int(forked.read_text()))
    assert project_bite.Shell().run(["sh", "-c", "echo tidy"], tmp_path)[:2] == (0, "tidy\n")


def test_the_first_copy_is_set_up_alone_so_the_others_find_what_it_cached(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "setups"
    setup = f"sh -c 'echo + >> {log}; sleep 0.2; echo - >> {log}'"
    gates = dict.fromkeys(("one", "two", "three"), LINT)
    registry = f'setup = ["{setup}"]\n' + "".join(bite(name, "a planted word") for name in gates)
    root = project(tmp_path / "repo", gates, registry)
    assert prove(root, "--jobs", "3") == 0
    assert shown(capsys).count(": bites\n") == 3
    lines = log.read_text(encoding="utf-8").split()
    assert lines[:2] == ["+", "-"]
    assert sorted(lines) == ["+"] * 3 + ["-"] * 3


def test_in_a_copy_the_change_is_what_the_tree_holds_beyond_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert "bite: changed: a planted word: bites" in shown(capsys)
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
    assert shown(capsys) == (
        "bite: green: this change is the adoption, whose base holds no binding; nothing is"
        " proven here, and `bite-all` proves every gate in tier 2\n"
    )
    assert list(scratch.iterdir()) == []
    assert prove(root) == 0


def test_a_stopped_shell_runs_nothing_more(tmp_path: Path) -> None:
    shell = project_bite.Shell()
    assert shell.run(["sh", "-c", "echo out; echo err >&2; exit 4"], tmp_path)[:2] == (
        4,
        "out\nerr\n",
    )
    shell.stop()
    assert shell.run(["sh", "-c", "exit 0"], tmp_path)[:2] == (130, "stopped")


def test_each_verdict_line_ends_with_its_wall_time_and_lane(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path / "repo", {"lint": f"sleep 0.2; {LINT}"}, bite("lint", "a planted word")
    )
    assert prove(root) == 0
    line = capsys.readouterr().out.splitlines()[1]
    match = re.fullmatch(r"bite: lint: a planted word: bites \((\d+\.\d)s, lane 0\)", line)
    assert match is not None
    assert float(match[1]) >= 0.4


def test_a_lane_is_named_on_the_lines_of_its_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = dict.fromkeys(("one", "two"), f"sleep 0.3; {LINT}")
    registry = "".join(bite(name, "a planted word") for name in gates)
    root = project(tmp_path / "repo", gates, registry)
    assert project_bite.main(["project_bite.py", "--root", str(root), "--jobs", "2"]) == 0
    lanes = set(re.findall(r"lane (\d)\)", capsys.readouterr().out))
    assert lanes == {"0", "1"}


@pytest.mark.parametrize(
    "gate",
    [
        pytest.param("sleep 5; exit 0", id="green"),
        pytest.param("sleep 5; echo L001; exit 1", id="red with the words"),
        pytest.param("sleep 5; exit 1", id="red"),
    ],
)
def test_a_gate_ended_by_the_time_limit_is_timed_out_whatever_its_exit_code_would_say(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], gate: str
) -> None:
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "a planted word"))
    assert prove(root, "--limit", "0.5") == 1
    out = shown(capsys)
    assert "bite: lint: a planted word: TIMED OUT\n" in out
    assert "bite: timed out after 0.5s" in out
    assert "bite: red: 0 of 1 faults bit" in out


def ceilings(**keys: float) -> str:
    return "[ceilings.seconds]\n" + "".join(f"{key} = {value}\n" for key, value in keys.items())


def limited_project(tmp_path: Path, tier: str, thresholds: str) -> Path:
    root = project(
        tmp_path / "repo",
        {"lint": f"sleep 3; {LINT}"},
        bite("lint", "a planted word"),
        rows=row("lint", tier),
    )
    write(root, "quality/thresholds.toml", thresholds)
    commit(root, "thresholds")
    return root


def test_a_tier_or_gate_ceiling_far_below_a_cold_run_never_limits_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = limited_project(tmp_path, "0", ceilings(lint=0.5, tier0=0.5, tier1=0.5))
    assert prove(root) == 0
    out = shown(capsys)
    assert "bite: lint: a planted word: bites" in out
    assert "timed out" not in out


def test_the_bite_run_ceiling_limits_every_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = limited_project(tmp_path, "1", ceilings(**{"bite-run": 0.5}))
    assert prove(root) == 1
    assert "bite: timed out after 0.5s" in shown(capsys)


def test_the_limit_flag_overrides_the_bite_run_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = limited_project(tmp_path, "1", ceilings(**{"bite-run": 60}))
    assert prove(root, "--limit", "0.5") == 1
    assert "bite: timed out after 0.5s" in shown(capsys)


def test_with_no_ceiling_at_all_a_run_is_limited_to_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    limits: set[float | None] = set()
    run = project_bite.Shell.run

    def watch(*args: object) -> project_bite.Run:
        limits.add(args[3])  # type: ignore[arg-type]
        return run(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(project_bite.Shell, "run", watch)
    assert prove(root) == 0
    assert limits == {900.0}


def test_a_run_ended_by_the_limit_has_an_integer_code_and_says_it_timed_out(
    tmp_path: Path,
) -> None:
    ran = project_bite.Shell().run(["sleep", "5"], tmp_path, 0.3)
    assert isinstance(ran.code, int)
    assert ran.timed_out


def test_a_process_that_left_the_group_holding_the_output_cannot_hold_the_run_past_the_limit(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    ran = project_bite.Shell().run(["sh", "-c", "setsid sleep 6 & sleep 30"], tmp_path, 0.3)
    assert ran.timed_out
    assert time.monotonic() - started < 4


def test_the_proof_names_the_limit_in_use_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    assert prove(root) == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        "bite: limit: 900s a run; no `bite-run` in quality/thresholds.toml"
    )
    write(root, "quality/thresholds.toml", ceilings(tier0=60, **{"bite-run": 600}))
    commit(root, "thresholds")
    assert prove(root) == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        "bite: limit: 600s a run, `bite-run` in quality/thresholds.toml"
    )
    assert prove(root, "--limit", "30") == 0
    assert capsys.readouterr().out.splitlines()[0] == "bite: limit: 30s a run"


def test_a_red_proof_ends_with_the_load_at_its_start_and_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": "exit 0"}, bite("lint", "a planted word"))
    assert prove(root) == 1
    last = capsys.readouterr().out.splitlines()[-1]
    assert re.fullmatch(r"bite: load: \d+\.\d on \d+ cores at the start, .* at the end", last)


def test_a_green_proof_prints_no_load(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    assert prove(root) == 0
    assert "bite: load:" not in capsys.readouterr().out


def test_a_missing_load_average_is_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "getloadavg")
    root = project(tmp_path / "repo", {"lint": "exit 0"}, bite("lint", "a planted word"))
    assert prove(root) == 1
    assert "bite: load: unknown at the start, unknown at the end" in capsys.readouterr().out


def test_a_red_run_over_twice_the_fastest_green_run_says_how_much_slower(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = 'if grep -rl planted src; then sleep 1; echo "no words"; exit 1; fi'
    root = project(tmp_path / "repo", {"lint": body}, bite("lint", "a planted word"))
    assert prove(root) == 1
    line = capsys.readouterr().out.splitlines()[1]
    assert re.search(
        r"RED FOR ANOTHER REASON \(\d+\.\ds, lane 0; \d+\.\dx this gate's fastest green run\)", line
    )


def test_a_red_run_no_slower_than_its_green_runs_says_nothing_more(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": "exit 0"}, bite("lint", "a planted word"))
    assert prove(root) == 1
    assert "fastest green run" not in capsys.readouterr().out


SLEEPY = f"sleep 0.3; {LINT}"
WHOLE_AND_NARROWED = bite("lint", "whole") + bite("lint", "narrowed") + 'args = ["src"]\n'
CURE = (
    "a bite of a whole-tree gate is narrowed: its args name one file, one target or one mutant,"
    " or its `whole` says why it cannot be"
)


def recorded(root: Path) -> list[dict[str, object]]:
    lines = (root / gatelib.STATE / "timing.jsonl").read_text(encoding="utf-8").splitlines()
    return [record for record in map(json.loads, lines) if record.get("kind") == "fault"]


def lint_config_changed(root: Path) -> None:
    changed(root)
    write(root, "lint.cfg", "rules = fewer\n")
    commit(root, "feat(core): a rule changed")


@pytest.mark.parametrize(
    ("prepare", "flags"),
    [
        pytest.param(None, (), id="whole"),
        pytest.param(None, ("--gate", "lint"), id="one gate"),
        pytest.param(None, ("--pull-request-gates",), id="the pull request's gates"),
        pytest.param(lint_config_changed, ("--changed",), id="changed"),
        pytest.param(None, ("--jobs", "2"), id="two lanes"),
    ],
)
def test_the_proof_records_each_fault_with_its_gate_its_seconds_and_whether_it_passes_args(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[Path], None] | None,
    flags: tuple[str, ...],
) -> None:
    monkeypatch.setenv("RATCHET_RUN", "run-7")
    gates = {"lint": SLEEPY, "format": SLEEPY}
    registry = WHOLE_AND_NARROWED + bite("format", "whole")
    root = project(tmp_path / "repo", gates, registry)
    if prepare is not None:
        prepare(root)
    assert prove(root, *flags) == 0
    found = recorded(root)
    gates_proved = ["lint"] if "--gate" in flags else ["format", "lint"]
    assert sorted((r["gate"], r["bite"], r["narrowed"], r["verdict"]) for r in found) == sorted(
        [("lint", "whole", False, "bites"), ("lint", "narrowed", True, "bites")]
        + ([("format", "whole", False, "bites")] if "format" in gates_proved else [])
    )
    assert all(isinstance(r["seconds"], float) and r["seconds"] >= 0.6 for r in found)
    assert {r["run"] for r in found} == {"run-7"}
    assert all(r["at"] and r["tree"] for r in found)


def test_a_fault_over_the_tier0_ceiling_whose_bite_passes_no_args_is_named_with_the_cure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": SLEEPY}, WHOLE_AND_NARROWED)
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    assert prove(root) == 0
    out = capsys.readouterr().out.splitlines()
    slow = [line for line in out if line.startswith("bite: slow: ")]
    assert len(slow) == 1, out
    pattern = r"bite: slow: lint: whole: \d+\.\ds with no args, over the tier0 ceiling of 0\.5s; "
    assert re.fullmatch(pattern + re.escape(CURE), slow[0]), slow[0]
    assert out.index(slow[0]) == len(out) - 2, "named after the verdicts, before the total"


@pytest.mark.parametrize(
    "thresholds",
    [
        pytest.param(None, id="no thresholds"),
        pytest.param(ceilings(tier1=0.1), id="no tier0 ceiling"),
        pytest.param(ceilings(tier0=60), id="under the ceiling"),
        pytest.param(ceilings(tier0=0), id="a ceiling of zero"),
        pytest.param(ceilings(tier0=-3), id="a ceiling below zero"),
        pytest.param("[ceilings.seconds\n", id="thresholds that do not parse"),
    ],
)
def test_no_fault_is_named_slow_without_a_tier0_ceiling_it_ran_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], thresholds: str | None
) -> None:
    root = project(tmp_path / "repo", {"lint": SLEEPY}, bite("lint", "whole"))
    if thresholds is not None:
        write(root, "quality/thresholds.toml", thresholds)
        commit(root, "thresholds")
    assert prove(root, "--limit", "60") == 0
    assert "bite: slow:" not in capsys.readouterr().out
    conformance.run(root)
    assert "slow:" not in capsys.readouterr().out


def test_a_bite_left_whole_on_purpose_is_named_with_its_reason_by_the_proof_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reason = "the runner filters after its own selection, so a narrowed run hides an emptied folder"
    registry = bite("lint", "whole") + f'whole = "{reason}"\n'
    root = project(tmp_path / "repo", {"lint": SLEEPY}, registry)
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    assert prove(root) == 0
    slow = [line for line in capsys.readouterr().out.splitlines() if "slow:" in line]
    assert len(slow) == 1
    assert slow[0].endswith(f"over the tier0 ceiling of 0.5s; whole on purpose: {reason}")
    conformance.run(root)
    assert "slow:" not in capsys.readouterr().out


def test_conformance_names_a_slow_fault_from_the_last_proof_until_its_bite_is_narrowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": SLEEPY}, WHOLE_AND_NARROWED)
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    conformance.run(root)
    assert "slow:" not in capsys.readouterr().out, "no record of a run is no finding"
    assert prove(root) == 0
    capsys.readouterr()
    assert conformance.run(root) == conformance.run(root)
    notes = [line for line in capsys.readouterr().out.splitlines() if line.startswith("slow:")]
    pattern = (
        r"slow: quality/bites\.toml: lint: whole: \d+\.\ds with no args, over the tier0 ceiling"
        r" of 0\.5s; "
    )
    assert len(notes) == 2, notes
    assert notes[0] == notes[1], "the same record gives the same note"
    assert re.fullmatch(pattern + re.escape(CURE), notes[0]), notes[0]
    narrowed = bite("lint", "whole") + 'args = ["src"]\n'
    write(root, REGISTRY, narrowed + bite("lint", "narrowed") + 'args = ["src"]\n')
    conformance.run(root)
    assert "slow:" not in capsys.readouterr().out, "a record of a bite since narrowed is stale"


def test_conformance_reads_only_the_last_whole_run_of_a_bite_that_still_passes_no_args(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": SLEEPY}, bite("lint", "whole"))
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    fault = {"kind": "fault", "gate": "lint", "bite": "whole", "narrowed": False}
    rows = [
        {**fault, "seconds": 9.0},
        {**fault, "seconds": 0.2},
        {**fault, "bite": "gone", "seconds": 9.0},
        {"key": "lint", "seconds": 9.0},
    ]
    timing = root / gatelib.STATE / "timing.jsonl"
    timing.parent.mkdir()
    timing.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    conformance.run(root)
    assert "slow:" not in capsys.readouterr().out, "the last run of the bite was quick"
    for last in ({**fault, "seconds": 9.0, "narrowed": True}, {**fault, "seconds": "9"}):
        timing.write_text(json.dumps(last) + "\n", encoding="utf-8")
        conformance.run(root)
        assert "slow:" not in capsys.readouterr().out, last


@pytest.mark.parametrize(("each", "named"), [(0.25, False), (0.3, True)], ids=["at", "over"])
def test_a_fault_is_slow_only_when_it_took_more_than_the_tier0_ceiling(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    each: float,
    named: bool,
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "whole"))
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    run = project_bite.Shell.run

    def timed(*args: object) -> project_bite.Run:
        return run(*args)._replace(seconds=each)  # type: ignore[arg-type]

    monkeypatch.setattr(project_bite.Shell, "run", timed)
    assert prove(root) == 0
    assert [record["seconds"] for record in recorded(root)] == [each * 2]
    assert ("bite: slow: lint: whole:" in capsys.readouterr().out) is named
    conformance.run(root)
    assert ("slow: quality/bites.toml: lint: whole:" in capsys.readouterr().out) is named


@pytest.mark.parametrize("args", ["args = []\n", 'args = [" "]\n'])
def test_args_that_name_nothing_narrow_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: str
) -> None:
    root = project(tmp_path / "repo", {"lint": SLEEPY}, bite("lint", "whole") + args)
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    assert prove(root) == 0
    assert [record["narrowed"] for record in recorded(root)] == [False]
    out = capsys.readouterr().out
    assert re.search(r"bite: slow: lint: whole: .*; " + re.escape(CURE), out), out
    conformance.run(root)
    assert "slow: quality/bites.toml: lint: whole:" in capsys.readouterr().out
    reason = "the runner filters after its own selection"
    write(root, REGISTRY, bite("lint", "whole") + args + f'whole = "{reason}"\n')
    assert project_bite.read_registry((root / REGISTRY).read_text())[1] == []
    conformance.run(root)
    assert "slow:" not in capsys.readouterr().out, "a reason beside args that narrow nothing"


@pytest.mark.parametrize(
    ("before", "after", "again"),
    [
        pytest.param("", 'whole = "a reason"\n', False, id="a reason added"),
        pytest.param('whole = "a reason"\n', 'whole = "another"\n', False, id="a reason edited"),
        pytest.param('args = ["src"]\n', 'whole = "a reason"\n', True, id="args to a reason"),
        pytest.param('whole = "a reason"\n', 'args = ["src"]\n', True, id="a reason to args"),
    ],
)
def test_a_change_to_whole_alone_proves_no_gate_again_and_a_swap_with_args_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, before: str, after: str, again: bool
) -> None:
    rest = bite("tests", WORD) + bite("slow", WORD)
    root = targeted(tmp_path, registry=bite("lint", WORD) + before + rest)
    write(root, REGISTRY, bite("lint", WORD) + after + rest)
    assert prove(root, "--changed") == 0
    assert (proved(shown(capsys).splitlines()) == ["lint"]) is again


def read_only_record(record: Path) -> None:
    record.parent.mkdir()
    record.write_text("", encoding="utf-8")
    record.chmod(0o444)


def read_only_folder(record: Path) -> None:
    record.parent.mkdir()
    record.parent.chmod(0o555)


def record_is_a_folder(record: Path) -> None:
    record.mkdir(parents=True)


def state_is_a_file(record: Path) -> None:
    record.parent.write_text("not a folder\n", encoding="utf-8")


def record_is_a_fifo(record: Path) -> None:
    record.parent.mkdir()
    os.mkfifo(record)


def record_is_linked_out(record: Path) -> None:
    outside = record.parents[2] / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    record.parent.mkdir()
    record.symlink_to(outside)


def state_is_linked_out(record: Path) -> None:
    outside = record.parents[2] / "outside"
    outside.mkdir()
    record.parent.symlink_to(outside)


def apart(script: str, *argv: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(Path(project_bite.__file__).with_name(script)), *argv]
    return subprocess.run(  # noqa: S603 # -- this interpreter and the plugin's own script
        command, capture_output=True, text=True, timeout=60, check=False
    )


def settled(out: str) -> list[str]:
    lines = AID.sub("", TIMING.sub("", out)).splitlines()
    return [re.sub(r" in \d+s$", "", line) for line in lines]


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only file or folder")
@pytest.mark.parametrize(
    "unwritable",
    [
        read_only_record,
        read_only_folder,
        record_is_a_folder,
        state_is_a_file,
        record_is_a_fifo,
        record_is_linked_out,
        state_is_linked_out,
    ],
)
def test_a_proof_that_cannot_record_says_so_once_and_is_otherwise_the_same_run(
    tmp_path: Path, unwritable: Callable[[Path], None]
) -> None:
    registry = bite("lint", "one") + bite("lint", "two") + bite("format", "three")

    def made(name: str) -> Path:
        root = project(tmp_path / name, {"lint": LINT, "format": LINT}, registry)
        write(root, ".gitignore", "ignored/\n*.secret\n.ratchet\n")
        commit(root, "the state folder is ignored")
        return root

    recorded_run = apart("project_bite.py", "--root", str(made("records")), "--jobs", "1")
    root = made("repo")
    unwritable(root / runrecord.TIMING)
    run = apart("project_bite.py", "--root", str(root), "--jobs", "1")
    said = [line for line in run.stdout.splitlines() if line.startswith("bite: not recorded: ")]
    assert len(said) == 1, run.stdout
    assert said[0].startswith(f"bite: not recorded: {root / runrecord.TIMING}: "), said
    assert (run.returncode, recorded_run.returncode) == (0, 0), run.stdout + run.stderr
    assert settled(run.stdout.replace(said[0] + "\n", "")) == settled(recorded_run.stdout)
    outside = [*tmp_path.glob("outside.jsonl"), *tmp_path.glob("outside/*")]
    assert [path.read_text() for path in outside] in ([], [""]), "nothing written outside"


def test_conformance_on_a_record_that_is_a_fifo_finishes_as_with_no_record(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "whole"))
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    without = apart("conformance.py", str(root))
    record_is_a_fifo(root / runrecord.TIMING)
    run = apart("conformance.py", str(root))
    assert (run.returncode, run.stdout) == (without.returncode, without.stdout), run.stderr


@pytest.mark.parametrize(
    "line",
    [
        '{"kind": "fault", "gate": ["lint"], "bite": "whole", "seconds": 9.0, "narrowed": false}',
        '{"kind": "fault", "gate": "lint", "bite": {"a": 1}, "seconds": 9.0, "narrowed": false}',
        '{"kind":"fault","gate":"lint","bite":"whole","seconds":Infinity,"narrowed":false}',
        "[" * 100_000,
    ],
    ids=["a gate that is a list", "a bite that is a table", "seconds without end", "deep"],
)
def test_conformance_reads_past_a_record_it_cannot_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "whole"))
    write(root, "quality/thresholds.toml", ceilings(tier0=0.5))
    commit(root, "thresholds")
    code = conformance.run(root)
    without = capsys.readouterr().out
    timing = root / runrecord.TIMING
    timing.parent.mkdir()
    timing.write_text(line + "\n", encoding="utf-8")
    assert conformance.run(root) == code
    assert capsys.readouterr().out == without


def test_a_gate_that_times_out_only_with_the_fault_planted_is_timed_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = f"if grep -rl planted src; then sleep 5; fi; {LINT}"
    root = project(tmp_path / "repo", {"lint": body}, bite("lint", "a planted word"))
    assert prove(root, "--limit", "1") == 1
    assert "bite: lint: a planted word: TIMED OUT\n" in shown(capsys)


def test_a_later_run_that_times_out_turns_a_bite_into_timed_out_not_not_proven(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    counted = tmp_path / "runs"
    body = f'echo x >> {counted}; if [ "$(wc -l < {counted})" -ge 3 ]; then sleep 5; fi; {LINT}'
    root = project(tmp_path / "repo", {"lint": body}, bite("lint", "a planted word"))
    assert prove(root, "--limit", "1") == 1
    assert "bite: lint: a planted word: TIMED OUT\n" in shown(capsys)


def test_a_repository_with_no_commit_cannot_be_copied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    git(tmp_path, "init", "-q", "-b", "main", str(root))
    write(root, "docs/process/binding.md", f"# Binding\n\n## Gates\n\n{TABLE}{row('lint')}")
    write(root, "quality/bites.toml", bite("lint", "a planted word"))
    assert prove(root) == 1
    out = shown(capsys)
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
        (bite("lint", "x") + 'whole = ""\n', "bite 1 (lint): 'whole' is a non-empty string"),
        (
            bite("lint", "x") + 'args = ["a"]\nwhole = "it cannot be narrowed"\n',
            "bite 1 (lint): a bite narrows its gate with 'args' or says in 'whole' why it cannot",
        ),
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
        (
            "quality/bites.toml: bite 'no benchmark' of gate 'perf' cannot be planted: no file git"
            " sees matches bench/**; write the globs to match the collection as it is now"
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


def test_a_run_around_the_copy_hands_its_run_id_to_no_gate_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATCHET_RUN", "the-tiers-run")
    assert "RATCHET_RUN" not in project_bite.environment()


@pytest.mark.parametrize("path", ["link/planted.txt", "file-link"])
def test_a_write_fault_does_not_follow_a_tracked_link_out_of_the_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("the owner's\n", encoding="utf-8")
    fault = f'write = {{ path = "{path}", content = "planted\\n" }}'
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "through a link", fault))
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "file-link").symlink_to(outside / "kept.txt")
    commit(root, "feat(core): two links out of the repository")
    assert prove(root) == 1
    assert f"{path} is reached through a symbolic link" in shown(capsys)
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
    out = shown(capsys)
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
    out = shown(capsys)
    assert f"include '{include}' leaves the repository through a symbolic link" in out
    assert "bites" not in out


READS_IGNORED = 'if grep -l planted x.secret 2>/dev/null; then echo "L001 planted"; exit 1; fi'
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


def links_to_a_directory(root: Path) -> None:
    (root / "dirlink").symlink_to("src", target_is_directory=True)
    commit(root, "feat(core): a tracked link to a directory")


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
        (
            'write = { path = "dirlink/code.txt", content = "planted\\n" }',
            links_to_a_directory,
            "dirlink/code.txt is reached through a symbolic link",
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
    patches = {
        "new-secret": "--- /dev/null\n+++ b/x.secret\n@@ -0,0 +1 @@\n+planted\n",
        "edit-secret": "--- a/env.secret\n+++ b/env.secret\n@@ -1 +1 @@\n-clean\n+planted\n",
        "rename-secret": "diff --git a/env.secret b/x.txt\nsimilarity index 50%\n"
        "rename from env.secret\nrename to x.txt\n--- a/env.secret\n+++ b/x.txt\n"
        "@@ -1 +1 @@\n-clean\n+planted\n",
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
    out = shown(capsys)
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
    out = shown(capsys)
    assert "bite: lint: by replace: DOES NOT BITE" in out
    assert "bite: lint: by write: DOES NOT BITE" in out


RETURNS = (
    "  the gate did not return to green once the fault was gone, so its red may come from what"
    " its own runs leave and not from the fault"
)


def test_each_bite_is_judged_against_a_green_run_of_its_own_just_before_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = (
        "test -f ignored/seen && { echo L001 still red; exit 1; }\n"
        "grep -rl planted src && { mkdir -p ignored; touch ignored/seen; echo L001; exit 1; }\n"
        "exit 0"
    )
    registry = bite("lint", "first") + bite("lint", "blind", BLIND)
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 1
    out = shown(capsys)
    assert f"bite: lint: first: NOT PROVEN\n{RETURNS}\n  L001 still red" in out
    assert "bite: lint: blind: RED BEFORE THE FAULT\n  L001 still red" in out


@pytest.mark.parametrize("keeps", ["in a directory git ignores", "outside the copy"])
def test_a_gate_red_again_with_no_fault_planted_proves_no_bite_before_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keeps: str
) -> None:
    count = "ignored/runs" if keeps == "in a directory git ignores" else str(tmp_path / "runs")
    gate = (
        f"mkdir -p ignored; n=$(cat {count} 2>/dev/null || echo 0); echo $((n + 1)) > {count}\n"
        'test "$n" -ge 1 && { echo "L001 on run $((n + 1))"; exit 1; }; exit 0'
    )
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "blind", BLIND))
    assert prove(root) == 1
    out = shown(capsys)
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
    patch = "--- a/src/code.txt\n+++ b/src/code.txt\n@@ -1 +1 @@\n-clean\n+planted\n"
    write(tmp_path / "repo", "quality/bites/p.patch", patch)
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "gone", fault))
    (root / "src/code.txt").unlink()
    write(root, "src/other.txt", "clean\n")
    assert prove(root) == 1
    out = shown(capsys)
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
    assert shown(capsys).count(": bites\n") == 2


def test_a_copy_that_cannot_be_put_back_as_it_stood_makes_the_run_red(
    tmp_path: Path, scratch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = f"mkdir -p locked; echo planted > locked/f; chmod 555 locked\n{LINT}"
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "first"))
    try:
        assert prove(root) == 1
    finally:
        for locked in scratch.rglob("locked"):
            locked.chmod(0o755)
    out = shown(capsys)
    assert "failed to remove locked" in out
    assert "bites" not in out


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
    out = shown(capsys)
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
    setup, gate, prepare, verdict = case
    root = project(
        tmp_path / "repo", {"lint": f"{gate}\nexit 0"}, setup + bite("lint", "blind", BLIND)
    )
    if prepare is not None:
        prepare(root)
    assert prove(root) == 1
    assert f"bite: lint: blind: {verdict}\n" in shown(capsys)


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
    out = shown(capsys)
    assert f"bite: lint: first: {verdict}\n" in out
    assert "bite: lint: blind: DOES NOT BITE\n" in out


def test_a_fault_inside_a_repository_setup_made_is_not_planted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    out = shown(capsys)
    assert "bite: lint: first: THE FAULT WAS NOT PLANTED\n" in out
    assert "vendor/x/planted.txt is inside vendor/x, a repository of its own" in out
    assert "bite: lint: blind: DOES NOT BITE\n" in out


def fingerprint(root: Path) -> list[tuple[str, int, bytes]]:
    entries = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or the_record(root, path):
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
    out = shown(capsys)
    assert "bite: lint: swapped: THE FAULT WAS NOT PLANTED\n" in out
    assert "is reached through a symbolic link; a fault changes the copy's own file" in out


@pytest.mark.parametrize(
    "case", [(kind, at) for kind in ("write", "overwrite", "remove") for at in ("src", "src/deep")]
)
def test_a_directory_swapped_once_the_walk_holds_it_still_takes_the_fault(
    tmp_path: Path,
    *,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str],
) -> None:
    kind, swapped = case
    write(tmp_path / "repo", INNER, "clean\n")
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "swapped", SWAPPED[kind]))
    before = fingerprint(root)
    copies: list[Path] = []
    checked, looked = project_bite.owned, project_bite.entry

    def record(copy: Path, *paths: str) -> None:
        copies.append(copy)
        checked(copy, *paths)

    def look_then_swap(here: int, name: str) -> os.stat_result | None:
        found = looked(here, name)
        if name in {"planted.txt", "inner.txt"} and not (copies[0] / "aside").exists():
            (copies[0] / swapped).rename(copies[0] / "aside")
            (copies[0] / swapped).symlink_to(root / swapped)
        return found

    monkeypatch.setattr(project_bite, "owned", record)
    monkeypatch.setattr(project_bite, "entry", look_then_swap)
    prove(root)
    assert fingerprint(root) == before
    assert "bite: lint: swapped: " in shown(capsys)


@pytest.mark.parametrize(
    "link",
    [
        "ln -s {root}/src src",
        "ln -s ../../../../../../../../../../../../../../.. src",
        "ln -s hop src && ln -s {root}/src hop",
    ],
    ids=["absolute", "relative", "chain"],
)
def test_a_setup_that_leaves_a_link_leading_out_of_the_copy_is_unsafe_and_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], link: str
) -> None:
    root = project(tmp_path / "repo", {"lint": "exit 0"}, "")
    setup = f"mv src aside && {link.format(root=root)}"
    (root / "quality/bites.toml").write_text(
        f"setup = [{json.dumps('sh -c ' + shlex.quote(setup))}]\n" + bite("lint", "f"),
        encoding="utf-8",
    )
    commit(root, "registry")
    before = fingerprint(root)
    assert prove(root) == 1
    assert fingerprint(root) == before
    out = shown(capsys)
    assert "setup left " in out
    assert "leads outside the scratch copy" in out


@pytest.mark.parametrize(
    "link",
    [
        "ln -s src alias",
        "ln -s ./src//. alias",
        "ln -s src/../src alias",
        'ln -s "$PWD/src" alias',
        'ln -s "$PWD" alias',
    ],
    ids=["relative", "dotted", "back up", "absolute", "the copy itself"],
)
def test_a_setup_link_that_stays_inside_the_copy_is_accepted_and_the_proof_goes_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], link: str
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, "")
    (root / "quality/bites.toml").write_text(
        f"setup = [{json.dumps('sh -c ' + shlex.quote(link))}]\n" + bite("lint", "a planted word"),
        encoding="utf-8",
    )
    commit(root, "registry")
    assert prove(root) == 0
    out = shown(capsys)
    assert "leads outside the scratch copy" not in out
    assert "bite: green" in out


def test_a_link_the_snapshot_holds_is_judged_though_the_directory_is_back_by_the_time_it_looks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path / "repo", {"lint": "exit 0"}, "")
    setup = f"mv src aside && ln -s {root / 'src'} src"
    (root / "quality/bites.toml").write_text(
        f"setup = [{json.dumps('sh -c ' + shlex.quote(setup))}]\n" + bite("lint", "f"),
        encoding="utf-8",
    )
    commit(root, "registry")
    before = fingerprint(root)
    looked = project_bite.contained

    def put_back_then_look(copy: Path, head: str) -> None:
        if (copy / "src").is_symlink():
            (copy / "src").unlink()
            (copy / "aside").rename(copy / "src")
        looked(copy, head)

    monkeypatch.setattr(project_bite, "contained", put_back_then_look)
    assert prove(root) == 1
    assert fingerprint(root) == before
    assert "leads outside the scratch copy" in shown(capsys)


@pytest.mark.parametrize("committed", [True, False], ids=["committed", "ignored"])
def test_a_link_reached_back_up_through_another_link_is_judged_by_where_the_first_leads(
    tmp_path: Path, *, capsys: pytest.CaptureFixture[str], committed: bool
) -> None:
    outside = tmp_path / "outside" / "sub"
    outside.mkdir(parents=True)
    root = project(tmp_path / "repo", {"lint": "exit 0"}, "")
    setup = 'ln -s "$PWD/L/../x" M'
    if committed:
        (root / "L").symlink_to(outside)
    else:
        setup = f'ln -s "{outside}" L && {setup}'
        (root / ".gitignore").write_text("ignored/\n*.secret\nL\n", encoding="utf-8")
    (root / "quality/bites.toml").write_text(
        f"setup = [{json.dumps('sh -c ' + shlex.quote(setup))}]\n" + bite("lint", "f"),
        encoding="utf-8",
    )
    commit(root, "registry")
    before = fingerprint(root)
    assert prove(root) == 1
    assert fingerprint(root) == before
    assert "setup left M" in shown(capsys)


def test_links_that_lead_round_in_a_ring_are_refused_as_leading_nowhere_safe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": "exit 0"}, "")
    (root / "quality/bites.toml").write_text(
        f"setup = [{json.dumps('sh -c ' + shlex.quote('ln -s b a && ln -s a b'))}]\n"
        + bite("lint", "f"),
        encoding="utf-8",
    )
    commit(root, "registry")
    assert prove(root) == 1
    assert "leads outside the scratch copy" in shown(capsys)


def made_copy(tmp_path: Path) -> tuple[Path, project_bite.Copy]:
    root = project(tmp_path / "repo", {"lint": LINT}, None)
    copy = project_bite.make_copy(
        root, tmp_path / "copy", registry=project_bite.Registry(), shell=project_bite.Shell()
    )
    (root / "src/junk.txt").write_text("real\n", encoding="utf-8")
    return root, copy


def test_a_directory_swapped_for_a_link_between_listing_and_restoring_never_reaches_the_real_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, copy = made_copy(tmp_path)
    (copy.path / "src/junk.txt").write_text("left by a gate\n", encoding="utf-8")
    (copy.path / "src/code.txt").write_text("changed\n", encoding="utf-8")
    before = fingerprint(root)
    at, swapped = project_bite.Tree.at, []

    def swapping(
        self: project_bite.Tree, path: str, **kwargs: bool
    ) -> AbstractContextManager[tuple[int, str]]:
        if not swapped and path.startswith("src/"):
            swapped.append(path)
            (copy.path / "src").rename(copy.path / "aside")
            (copy.path / "src").symlink_to(root / "src", target_is_directory=True)
        return at(self, path, **kwargs)

    monkeypatch.setattr(project_bite.Tree, "at", swapping)
    with pytest.raises(project_bite.Unprovable, match="reached through a symbolic link"):
        copy.restore()
    assert swapped
    assert fingerprint(root) == before
    assert (root / "src/junk.txt").read_text(encoding="utf-8") == "real\n"


def test_a_file_a_gate_changed_to_the_same_length_with_its_times_put_back_is_restored(
    tmp_path: Path,
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, None)
    old = 1_000_000_000_000_000_000
    os.utime(root / "src/code.txt", ns=(old, old))
    copy = project_bite.make_copy(
        root, tmp_path / "copy", registry=project_bite.Registry(), shell=project_bite.Shell()
    )
    target = copy.path / "src/code.txt"
    assert target.stat().st_mtime_ns == old
    target.write_text("dirty\n", encoding="utf-8")
    os.utime(target, ns=(old, old))
    copy.restore()
    assert target.read_text(encoding="utf-8") == "clean\n"


def test_a_restore_leaves_the_times_of_a_file_no_run_changed(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, None)
    old = 1_000_000_000_000_000_000
    os.utime(root / "src/code.txt", ns=(old, old))
    copy = project_bite.make_copy(
        root, tmp_path / "copy", registry=project_bite.Registry(), shell=project_bite.Shell()
    )
    untouched = copy.path / "src/code.txt"
    changed = copy.path / "tests/one.t"
    changed.write_text("changed\n", encoding="utf-8")
    copy.restore()
    assert changed.read_text(encoding="utf-8") == "a test\n"
    assert untouched.stat().st_mtime_ns == old


def test_a_tracked_file_an_ignore_rule_also_matches_is_restored(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, None)
    write(root, "kept.secret", "clean\n")
    git(root, "add", "--force", "kept.secret")
    commit(root, "feat(core): a tracked file the ignore rules match")
    copy = project_bite.make_copy(
        root, tmp_path / "copy", registry=project_bite.Registry(), shell=project_bite.Shell()
    )
    target = copy.path / "kept.secret"
    target.write_text("dirty\n", encoding="utf-8")
    copy.restore()
    assert target.read_text(encoding="utf-8") == "clean\n"


def test_a_link_standing_where_a_directory_belongs_is_replaced_and_the_real_tree_is_untouched(
    tmp_path: Path,
) -> None:
    root, copy = made_copy(tmp_path)
    (copy.path / "src/code.txt").write_text("changed\n", encoding="utf-8")
    (copy.path / "src").rename(copy.path / "aside")
    (copy.path / "src").symlink_to(root / "src", target_is_directory=True)
    before = fingerprint(root)
    copy.restore()
    assert fingerprint(root) == before
    assert not (copy.path / "src").is_symlink()
    assert (copy.path / "src/code.txt").read_text(encoding="utf-8") == "clean\n"
    assert not (copy.path / "src/junk.txt").exists()


def test_a_tracked_link_a_gate_pointed_elsewhere_is_restored_as_the_link_it_was(
    tmp_path: Path,
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, None)
    (root / "src/link").symlink_to("code.txt")
    commit(root, "a link")
    copy = project_bite.make_copy(
        root, tmp_path / "copy", registry=project_bite.Registry(), shell=project_bite.Shell()
    )
    link = copy.path / "src/link"
    link.unlink()
    link.symlink_to("elsewhere")
    copy.restore()
    assert link.is_symlink()
    assert str(link.readlink()) == "code.txt"


def test_a_repository_setup_made_is_left_as_its_own_history_moved_it(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, None)
    setup = (
        "sh -c 'git init -q nested && git -C nested -c user.name=s -c user.email=s@example.invalid"
        " commit -q --allow-empty -m one'"
    )
    copy = project_bite.make_copy(
        root,
        tmp_path / "copy",
        registry=project_bite.Registry(setup=(setup,)),
        shell=project_bite.Shell(),
    )
    nested = copy.path / "nested"
    git(nested, *project_bite.IDENTITY, "commit", "-q", "--allow-empty", "-m", "two")
    moved = git(nested, "rev-parse", "HEAD")
    copy.restore()
    assert git(nested, "rev-parse", "HEAD") == moved


def test_what_stands_in_the_way_of_a_tracked_path_is_replaced_by_it(tmp_path: Path) -> None:
    root, copy = made_copy(tmp_path)
    shutil.rmtree(copy.path / "src")
    (copy.path / "src").write_text("a file where a directory belongs\n", encoding="utf-8")
    (copy.path / "tests/one.t").unlink()
    (copy.path / "tests/one.t").mkdir()
    (copy.path / "tests/one.t/inner").write_text("x\n", encoding="utf-8")
    (copy.path / "gate").unlink()
    (copy.path / "gate").symlink_to(root / "src/junk.txt")
    before = fingerprint(root)
    copy.restore()
    assert fingerprint(root) == before
    assert (copy.path / "src/code.txt").read_text(encoding="utf-8") == "clean\n"
    assert (copy.path / "tests/one.t").read_text(encoding="utf-8") == "a test\n"
    assert (copy.path / "gate").is_file()
    assert not (copy.path / "gate").is_symlink()


def test_a_system_that_cannot_open_a_directory_without_following_a_link_plants_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_bite, "DESCRIPTORS", False)
    root = project(tmp_path / "repo", {"lint": LINT}, bite("lint", "a planted word"))
    before = fingerprint(root)
    assert prove(root) == 1
    assert shown(capsys) == f"{project_bite.UNSUPPORTED}\nbite: red\n"
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
    out = shown(capsys)
    assert "bite: lint: deleted: bites\n" in out
    assert (
        "bite: lint: a link: THE FAULT WAS NOT PLANTED\n  src/link would be a symbolic link or a"
        " submodule, and a fault writes a file\n" in out
    )


def test_the_closing_run_runs_once_for_each_set_of_args_among_the_bites_that_bit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "runs"
    gate = f'echo "${{2:-none}}" >> {log}\n{LINT}'
    registry = (
        bite("lint", "a", f'{PLANT}\nargs = ["a"]')
        + bite("lint", "b", f'{PLANT}\nargs = ["b"]')
        + bite("lint", "a again", f'{PLANT}\nargs = ["a"]')
        + bite("lint", "c, blind", f'{BLIND}\nargs = ["c"]')
    )
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 1
    assert "bite: lint: c, blind: DOES NOT BITE" in shown(capsys)
    assert log.read_text(encoding="utf-8").split() == [*"aabbaacc", "a", "b"]


def test_a_gate_stopped_during_its_closing_run_prints_no_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs, started = tmp_path / "runs", tmp_path / "started"
    until = int(time.time()) + 60
    gate = (
        f"n=$(($(cat {runs} 2>/dev/null || echo 0) + 1)); echo $n > {runs}\n"
        f'test $n -ge 3 && while [ "$(date +%s)" -lt {until} ]; do touch {started}; sleep 0.05;'
        f" done\n{LINT}"
    )
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "a planted word"))

    def interrupt() -> None:
        while not started.exists():
            time.sleep(0.01)
        signal.pthread_kill(threading.main_thread().ident or 0, signal.SIGTERM)

    threading.Thread(target=interrupt, daemon=True).start()
    assert prove(root) == 128 + 15
    assert runs.read_text(encoding="utf-8") == "3\n"
    assert shown(capsys) == "bite: red: interrupted by signal 15\n"


@pytest.mark.parametrize(
    "case",
    [
        ('write = { path = ".gitignore", content = "planted\\n" }', "grep -l planted .gitignore"),
        (
            'replace = { path = "src/.gitattributes", old = "text", new = "planted" }',
            "grep -l planted src/.gitattributes",
        ),
        ('remove = ["src/.gitattributes"]', "test ! -e src/.gitattributes"),
    ],
    ids=["a write over .gitignore", "a replace in .gitattributes", "a remove of .gitattributes"],
)
def test_a_rules_file_that_exists_may_be_changed_or_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str, str]
) -> None:
    fault, red = case
    write(tmp_path / "repo", "src/.gitattributes", "*.txt text\n")
    gate = f'{red} && {{ echo "L001 rules changed"; exit 1; }}; exit 0'
    root = project(
        tmp_path / "repo",
        {"lint": gate},
        bite("lint", "rules", fault) + bite("lint", "blind", BLIND),
    )
    assert prove(root) == 1
    out = shown(capsys)
    assert "bite: lint: rules: bites\n" in out
    assert "bite: lint: blind: DOES NOT BITE\n" in out


def test_a_copy_its_directories_cannot_reach_is_red_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = "setup = [\"sh -c 'rm -r .git/info && echo x > .git/info'\"]\n" + bite("lint", "a")
    root = project(tmp_path / "repo", {"lint": LINT}, registry)
    assert prove(root) == 1
    assert shown(capsys) == "[Errno 20] Not a directory: '.git/info'\nbite: red\n"


def test_a_chain_of_links_longer_than_any_real_one_is_refused_though_it_ends_inside_the_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": "exit 0"}, "")
    steps = ["ln -s . l45", *(f"ln -s l{n + 1} l{n}" for n in range(44, 0, -1))]
    setup = json.dumps("sh -c " + shlex.quote(" && ".join(steps)))
    (root / "quality/bites.toml").write_text(
        f"setup = [{setup}]\n" + bite("lint", "f"), encoding="utf-8"
    )
    commit(root, "registry")
    assert prove(root) == 1
    assert "a symbolic link that leads outside the scratch copy" in shown(capsys)


def test_a_rule_a_gate_adds_to_the_exclude_file_does_not_hide_what_it_left_from_the_next_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = (
        "test -e hidden && { echo L001 left behind; exit 1; }\n"
        "echo hidden > .git/info/exclude; echo planted > hidden\n"
        f"{LINT}"
    )
    registry = bite("lint", "first") + bite("lint", "second")
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 0
    assert shown(capsys).count(": bites\n") == 2


def test_a_cell_naming_the_plugin_script_and_a_tool_of_the_repository_is_bound() -> None:
    cell = "`ratchet` plugin `scripts/pins.py` and a linter of our own"
    table = f"# Binding\n\n## Gates\n\n{TABLE}{row('mixed', tool=cell)}\n## Speed\n\n| a |\n"
    assert [(r.gate, r.bound) for r in project_bite.gate_rows(table)] == [("mixed", True)]


def test_a_collection_a_fault_empties_has_no_directory_left_either(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gates = {"tests": 'test -d tests || { echo "L001 no directory"; exit 1; }'}
    root = project(tmp_path / "repo", gates, bite("tests", "no tests", 'remove = ["tests/**"]'))
    assert prove(root) == 0
    assert "bite: tests: no tests: bites" in shown(capsys)


def test_a_tracked_file_an_ignore_rule_matches_is_a_file_a_fault_may_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = "grep -l planted tracked.secret && { echo L001; exit 1; }; exit 0"
    fault = 'write = { path = "tracked.secret", content = "planted\\n" }'
    root = project(tmp_path / "repo", {"lint": gate}, bite("lint", "tracked", fault))
    write(root, "tracked.secret", "clean\n")
    git(root, "add", "-f", "tracked.secret")
    commit(root, "feat(core): a tracked file an ignore rule matches")
    assert prove(root) == 0
    assert "bite: lint: tracked: bites" in shown(capsys)


def test_what_a_gate_staged_is_not_left_staged_for_the_next_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = (
        'git diff --cached --quiet || { echo "left staged"; exit 1; }\n'
        "echo x > extra.txt; git add extra.txt\n"
        f"{LINT}"
    )
    registry = bite("lint", "first") + bite("lint", "second")
    root = project(tmp_path / "repo", {"lint": gate}, registry)
    assert prove(root) == 0
    assert shown(capsys).count(": bites\n") == 2


def test_a_setup_that_replaces_the_copy_with_another_directory_of_its_name_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo", {"lint": LINT}, "")
    swap = 'd="$PWD"; cd /; mv "$d" "$d.aside" && cp -a "$d.aside" "$d"'
    setup = json.dumps("sh -c " + shlex.quote(swap))
    (root / "quality/bites.toml").write_text(
        f"setup = [{setup}]\n" + bite("lint", "f"), encoding="utf-8"
    )
    commit(root, "registry")
    assert prove(root) == 1
    assert "is no longer the directory the scratch copy was made in" in shown(capsys)


def test_a_patch_fault_rewritten_to_edit_another_file_is_targeted_by_both_files(
    tmp_path: Path,
) -> None:
    root = patched(tmp_path)
    write(root, "quality/bites/p.patch", PATCH_FILE.replace("src/code.txt", "src/other.txt"))
    named_by(project_bite.fault_targets(root, "main"), "src/code.txt", "src/other.txt")
