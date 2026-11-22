import shlex
import sys
from pathlib import Path

import pytest

import test_stability
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]
GATES = (
    "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
    "| unit | a tool | 1 | | `just test` |\n"
    "| end to end | a tool | 2 | | `just e2e` |\n"
)
UNIT = "| unit | `just test` | `tests/unit/**` | `just test-files` |\n"
E2E = "| e2e | `just e2e` | `tests/e2e/**` | `just e2e` |\n"
HEAD = "| Suite | Gate | Files | Run files |\n| --- | --- | --- | --- |\n"
RUNNER = """
import os
import signal
import sys

log = os.environ["STABILITY_LOG"]
with open(log, "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
fail_on = os.environ.get("FAIL_ON_SEED", "")
if fail_on and any(fail_on in arg for arg in sys.argv[1:]):
    sys.exit(int(os.environ.get("FAIL_CODE", "4")))
if os.environ.get("KILL_SIGNAL"):
    os.kill(os.getpid(), int(os.environ["KILL_SIGNAL"]))
"""


def binding(*rows: str) -> str:
    return f"# Binding\n\n## Gates\n\n{GATES}\n## Tests\n\n{HEAD}{''.join(rows)}\n## Speed\n"


def project(tmp_path: Path, *rows: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    repository(
        root,
        {
            "docs/process/binding.md": binding(*rows),
            "tests/unit/a_test.py": "a\n",
            "tests/e2e/b_test.py": "b\n",
            "src/code.py": "c\n",
        },
    )
    branch(root)
    return root


def runner_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "runner.py"
    script.write_text(RUNNER.lstrip(), encoding="utf-8")
    log = tmp_path / "calls.log"
    monkeypatch.setenv("STABILITY_LOG", str(log))
    monkeypatch.delenv("FAIL_ON_SEED", raising=False)
    monkeypatch.delenv("FAIL_CODE", raising=False)
    monkeypatch.delenv("KILL_SIGNAL", raising=False)
    return script


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def run(*args: str) -> int:
    return test_stability.main(["test_stability.py", *args])


def test_default_runs_three_times_with_distinct_positive_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    code = run("--root", str(tmp_path), "--", sys.executable, str(script), "--seed={seed}")
    assert code == 0
    lines = calls(tmp_path)
    assert len(lines) == 3
    seeds = [int(line.split("=", 1)[1]) for line in lines]
    assert len(set(seeds)) == 3
    assert all(1 <= seed <= test_stability.MAX_SEED for seed in seeds)
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"test_stability: run 1/3 seed {seeds[0]}",
        f"test_stability: run 2/3 seed {seeds[1]}",
        f"test_stability: run 3/3 seed {seeds[2]}",
        "test_stability: green: 3 shuffled runs passed",
    ]


def test_generated_seeds_skip_duplicates_from_rng(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    values = iter([0, 0, 1])
    monkeypatch.setattr(test_stability.secrets, "randbelow", lambda _limit: next(values))
    code = run(
        "--root",
        str(tmp_path),
        "--runs",
        "2",
        "--",
        sys.executable,
        str(script),
        "{seed}",
    )
    assert code == 0
    assert calls(tmp_path) == ["1", "2"]


def test_explicit_seeds_run_in_order_and_override_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    code = run(
        "--root",
        str(tmp_path),
        "--runs",
        "5",
        "--seed",
        "17",
        "--seed",
        "42",
        "--",
        sys.executable,
        str(script),
        "-s",
        "{seed}",
    )
    assert code == 0
    assert calls(tmp_path) == ["-s 17", "-s 42"]
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "test_stability: run 1/2 seed 17",
        "test_stability: run 2/2 seed 42",
        "test_stability: green: 2 shuffled runs passed",
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["--runs", "0", "--", "echo", "{seed}"],
        ["--runs", "-2", "--", "echo", "{seed}"],
        ["--runs", "nope", "--", "echo", "{seed}"],
        ["--seed", "0", "--", "echo", "{seed}"],
        ["--seed", "-1", "--", "echo", "{seed}"],
        ["--seed", "abc", "--", "echo", "{seed}"],
        ["--"],
        [],
        ["--", "echo", "no-placeholder"],
        ["--suite", "unit", "--", "echo", "{seed}"],
    ],
)
def test_invalid_arguments_exit_2(args: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        run(*args)
    assert caught.value.code == 2


def test_failing_run_stops_early_and_prints_replay_with_failing_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    monkeypatch.setenv("FAIL_ON_SEED", "222")
    monkeypatch.setenv("FAIL_CODE", "4")
    code = run(
        "--root",
        str(tmp_path),
        "--seed",
        "111",
        "--seed",
        "222",
        "--seed",
        "333",
        "--",
        sys.executable,
        str(script),
        "--seed={seed}",
    )
    assert code == 4
    assert calls(tmp_path) == ["--seed=111", "--seed=222"]
    out = capsys.readouterr().out.splitlines()
    expected = shlex.join([sys.executable, str(script), "--seed=222"])
    assert out == [
        "test_stability: run 1/3 seed 111",
        "test_stability: run 2/3 seed 222",
        f"replay: {expected}",
        "test_stability: red: failed on run 2/3 with seed 222",
    ]


def test_killed_run_reports_signal_and_prints_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    monkeypatch.setenv("KILL_SIGNAL", "9")
    code = run(
        "--root",
        str(tmp_path),
        "--seed",
        "99",
        "--",
        sys.executable,
        str(script),
        "{seed}",
    )
    assert code == 137
    out = capsys.readouterr().out.splitlines()
    expected = shlex.join([sys.executable, str(script), "99"])
    assert out == [
        "test_stability: run 1/1 seed 99",
        "test_stability: killed by SIGKILL",
        f"replay: {expected}",
        "test_stability: red: failed on run 1/1 with seed 99",
    ]


def test_unstartable_command_returns_127_and_prints_replay(tmp_path: Path, capsys: Out) -> None:
    code = run("--root", str(tmp_path), "--seed", "5", "--", "no-such-stability-cmd", "{seed}")
    assert code == 127
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "test_stability: run 1/1 seed 5"
    assert out[1].startswith("test_stability: cannot run no-such-stability-cmd:")
    assert out[2] == "replay: no-such-stability-cmd 5"


def test_changed_runs_all_suites_including_tier_1_and_prints_replay_with_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    root = project(tmp_path, UNIT, E2E)
    write(root, "tests/unit/a_test.py", "edited\n")
    write(root, "tests/unit/with space_test.py", "new\n")
    write(root, "tests/e2e/b_test.py", "edited\n")
    write(root, "src/code.py", "edited\n")
    commit(root, "feat(core): touch both suites")
    monkeypatch.setenv("FAIL_ON_SEED", "77")
    code = run(
        "--root",
        str(root),
        "--changed",
        "--seed",
        "77",
        "--",
        sys.executable,
        str(script),
        "--seed={seed}",
    )
    assert code == 4
    expected_files = [
        "tests/e2e/b_test.py",
        "tests/unit/a_test.py",
        "tests/unit/with space_test.py",
    ]
    assert calls(tmp_path) == [" ".join(["--seed=77", *expected_files])]
    out = capsys.readouterr().out.splitlines()
    expected_replay = shlex.join([sys.executable, str(script), "--seed=77", *expected_files])
    assert f"replay: {expected_replay}" in out


def test_changed_splices_files_token_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    root = project(tmp_path, UNIT)
    write(root, "tests/unit/a_test.py", "edited\n")
    code = run(
        "--root",
        str(root),
        "--changed",
        "--seed",
        "12",
        "--",
        sys.executable,
        str(script),
        "--seed={seed}",
        "{files}",
        "-x",
    )
    assert code == 0
    assert calls(tmp_path) == ["--seed=12 tests/unit/a_test.py -x"]


def test_suite_filter_scopes_changed_files_and_skips_unmatched_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    root = project(tmp_path, UNIT, E2E)
    write(root, "tests/unit/a_test.py", "edited\n")
    write(root, "tests/e2e/b_test.py", "edited\n")
    code = run(
        "--root",
        str(root),
        "--changed",
        "--suite",
        "unit",
        "--seed",
        "3",
        "--",
        sys.executable,
        str(script),
        "{seed}",
    )
    assert code == 0
    assert calls(tmp_path) == ["3 tests/unit/a_test.py"]
    capsys.readouterr()

    root2 = project(tmp_path / "other", UNIT, E2E)
    write(root2, "tests/unit/a_test.py", "edited\n")
    code2 = run(
        "--root",
        str(root2),
        "--changed",
        "--suite",
        "e2e",
        "--seed",
        "3",
        "--",
        sys.executable,
        str(script),
        "{seed}",
    )
    assert code2 == 0
    assert "test_stability: green: no test file of the suites in scope (e2e) changed" in (
        capsys.readouterr().out
    )


def test_unknown_suite_is_red_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    root = project(tmp_path, UNIT)
    code = run(
        "--root",
        str(root),
        "--changed",
        "--suite",
        "missing",
        "--seed",
        "3",
        "--",
        sys.executable,
        str(script),
        "{seed}",
    )
    assert code == 1
    assert calls(tmp_path) == []
    assert "test_stability: red: unknown suite(s): missing" in capsys.readouterr().out


def test_changed_with_no_base_or_no_binding_or_clashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    single = tmp_path / "single"
    single.mkdir()
    repository(single, {"docs/process/binding.md": binding(UNIT), "tests/unit/a_test.py": "a\n"})
    assert (
        run(
            "--root",
            str(single),
            "--changed",
            "--seed",
            "1",
            "--",
            sys.executable,
            str(script),
            "{seed}",
        )
        == 0
    )
    assert "there is no commit to compare the tree with" in capsys.readouterr().out

    bare = tmp_path / "bare"
    bare.mkdir()
    repository(bare)
    assert (
        run(
            "--root",
            str(bare),
            "--changed",
            "--seed",
            "1",
            "--",
            sys.executable,
            str(script),
            "{seed}",
        )
        == 1
    )
    assert "has no '## Tests' section" in capsys.readouterr().out

    both = "| wide | `just e2e` | `tests/**` | `just e2e` |\n"
    clashed = project(tmp_path / "clash", UNIT, both)
    write(clashed, "tests/unit/a_test.py", "edited\n")
    assert (
        run(
            "--root",
            str(clashed),
            "--changed",
            "--suite",
            "unit",
            "--seed",
            "1",
            "--",
            sys.executable,
            str(script),
            "{seed}",
        )
        == 1
    )
    assert "matches the suites 'unit', 'wide'" in capsys.readouterr().out
    assert calls(tmp_path) == []


def test_changed_with_base_ref_and_empty_suites_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    script = runner_script(tmp_path, monkeypatch)
    root = project(tmp_path / "base", UNIT)
    write(root, "tests/unit/a_test.py", "edited\n")
    commit(root, "feat(core): first")
    first = git(root, "rev-parse", "HEAD")
    write(root, "tests/unit/other_test.py", "other\n")
    commit(root, "feat(core): second")
    code = run(
        "--root",
        str(root),
        "--base",
        first,
        "--changed",
        "--seed",
        "8",
        "--",
        sys.executable,
        str(script),
        "{seed}",
    )
    assert code == 0
    assert calls(tmp_path) == ["8 tests/unit/other_test.py"]

    empty = project(tmp_path / "empty", "| none: no tests yet | none | none | none |\n")
    write(empty, "tests/unit/a_test.py", "edited\n")
    assert (
        run(
            "--root",
            str(empty),
            "--changed",
            "--seed",
            "8",
            "--",
            sys.executable,
            str(script),
            "{seed}",
        )
        == 0
    )
    assert "no test file of the suites in scope (none) changed" in capsys.readouterr().out
