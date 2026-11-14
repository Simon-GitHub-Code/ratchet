import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from first_hour import matrix, persona
from first_hour import owner as owner_rules
from first_hour.persona import Persona, Slice

PIECE = Slice("html-form", "d", "v", "e")
SHIPPED = ("dotnet-chores", "go-todo-cli", "kotlin-recipes", "ruby-plants", "rust-shortener")

METRICS: dict[str, object] = {
    "stop": {"reason": "usable-merged", "minute": 61.5},
    "minutes": {"to_first_feature_merge": 41.0, "to_first_usable_merge": 44.2},
    "questions": {"total": 9, "product": 5, "default_able": 4},
    "prompts": {"guard": 3},
    "merges": [{"how": "hand"}, {"how": "agent"}, {"how": "agent"}],
    "hand_merges": 1,
    "architecture": {
        "read": True,
        "asked": True,
        "options": 3,
        "recommended": True,
        "adr": {"complete": True},
        "parts_declared": 2,
        "features": {"with_design": 1, "specs": 2},
        "reviews": {"design_issues": 1, "mentions": 2},
        "change_questions": 0,
    },
    "stack_leaks": {"count": 2, "names": ["Go", "pytest"]},
}


def owner(name: str, stack: str = "Go, a tool") -> Persona:
    return Persona(name, "idea", "profile", PIECE, stack=stack)


def test_the_shipped_personas_each_state_a_stack_and_their_slice_is_checkable() -> None:
    stacks = {n: persona.load(n).stack for n in persona.names() if persona.load(n).stack}
    assert tuple(sorted(stacks)) == SHIPPED
    assert persona.load("read-later").stack == ""
    for name in SHIPPED:
        one = persona.load(name)
        assert one.name == name
        assert one.slice.kind in {"html-form", "cli"}
        assert one.slice.expect in one.slice.value + one.slice.then or one.slice.kind == "cli"
        assert "Local only" in one.profile
        assert "my answer is no" in one.profile
    assert persona.load("go-todo-cli").slice.then == "list"
    assert persona.load("read-later").slice.then == ""


def test_the_owners_rule_lets_a_stated_stack_make_a_language_question_a_product_question() -> None:
    text = owner_rules.system_prompt(persona.load("rust-shortener"))
    assert "It must be written in Rust" in text
    assert "firm position names a language" in text


def test_a_runs_command_names_its_persona_directory_and_budget(tmp_path: Path) -> None:
    settings = matrix.Settings(tmp_path / "plugin", 90.0, 40.0, "opus", "haiku")
    where = tmp_path / "m" / "go-todo-cli"
    argv = matrix.command(Path("/x/run.py"), settings, persona="go-todo-cli", directory=where)
    assert argv[:4] == ["uv", "run", "/x/run.py", "run"]
    pairs = dict(zip(argv[4::2], argv[5::2], strict=True))
    assert pairs == {
        "--plugin": str(tmp_path / "plugin"),
        "--dir": str(tmp_path / "m" / "go-todo-cli"),
        "--persona": "go-todo-cli",
        "--minutes": "90",
        "--usd": "40",
        "--model": "opus",
        "--owner-model": "haiku",
    }


def test_a_row_reads_the_measures_of_one_run() -> None:
    cells = matrix.row(owner("go-todo-cli"), METRICS)
    assert len(cells) == len(matrix.HEADER)
    assert cells[:2] == ["go-todo-cli", "Go, a tool"]
    assert cells[2] == "usable-merged at 61.5 min"
    assert cells[3:5] == ["41.0", "44.2"]
    assert cells[5:9] == ["9", "5 / 4", "3", "2"]
    assert cells[9] == (
        "asked yes (3 options, recommended yes); record yes; parts 2; design 1/2;"
        " axis F 1 issues, 2 mentions; change questions 0"
    )
    assert cells[10:] == ["2 (Go, pytest)", "yes"]


def test_a_run_with_no_measurements_a_failed_one_and_a_thin_one_each_have_a_row() -> None:
    lost = matrix.row(owner("a", stack=""), None)
    assert lost[:3] == ["a", "unstated", "no measurements"]
    assert set(lost[3:]) == {"n/a"}
    assert matrix.row(owner("a"), None, "exit 3")[2] == "exit 3"
    thin = matrix.row(
        owner("a"),
        {"architecture": {"asked": False}, "merges": [], "stack_leaks": {"count": None}},
    )
    assert thin[9] == "asked no; repository not read"
    assert thin[10:] == ["not read", "no"]
    assert thin[8] == "0"
    clean = matrix.row(owner("a"), {**METRICS, "stack_leaks": {"count": 0, "names": []}})
    assert clean[10] == "0"
    pipe = matrix.row(owner("a", stack="a|b"), METRICS)
    assert pipe[1] == "a/b"


def test_the_table_has_one_row_a_persona_under_the_header() -> None:
    text = matrix.table([["a"] * len(matrix.HEADER), ["b"] * len(matrix.HEADER)])
    lines = text.splitlines()
    assert lines[0] == "| " + " | ".join(matrix.HEADER) + " |"
    assert lines[1].count("---") == len(matrix.HEADER)
    assert len(lines) == 4


def test_the_runs_go_one_after_another_and_the_table_is_written_after_each(tmp_path: Path) -> None:
    root = tmp_path / "matrix"
    order: list[str] = []
    seen: list[str] = []

    def launch(one: Persona, directory: Path) -> int:
        order.append(one.name)
        seen.append((root / "matrix.md").read_text() if (root / "matrix.md").exists() else "")
        assert directory == root / one.name
        if one.name != "b":
            directory.mkdir(parents=True)
            (directory / "metrics.json").write_text(json.dumps(METRICS), encoding="utf-8")
        return 0 if one.name != "c" else 7

    rows = matrix.run_all([owner("a"), owner("b"), owner("c")], root, launch)
    assert order == ["a", "b", "c"]
    assert seen[0] == ""
    assert seen[1].count("\n") == 3  # the header, its rule and the first row
    assert [r[0] for r in rows] == ["a", "b", "c"]
    assert rows[1][2] == "no measurements"
    assert rows[2][2] == "exit 7; usable-merged at 61.5 min"
    kept = json.loads((root / "matrix.json").read_text())
    assert set(kept) == {"a", "b", "c"}
    assert kept["b"] is None
    assert (root / "matrix.md").read_text().count("\n") == 5


def test_a_failing_run_still_gets_a_row_with_its_exit_code(tmp_path: Path) -> None:
    rows = matrix.run_all([owner("a")], tmp_path / "m", lambda _p, _d: 3)
    assert rows[0][2] == "exit 3"


def test_an_interrupted_matrix_keeps_the_rows_it_finished(tmp_path: Path) -> None:
    def launch(one: Persona, directory: Path) -> int:
        if one.name == "b":
            raise KeyboardInterrupt
        directory.mkdir(parents=True)
        (directory / "metrics.json").write_text(json.dumps(METRICS), encoding="utf-8")
        return 0

    with pytest.raises(KeyboardInterrupt):
        matrix.run_all([owner("a"), owner("b")], tmp_path / "m", launch)
    assert "| a |" in (tmp_path / "m" / "matrix.md").read_text()


def test_metrics_that_are_missing_or_not_an_object_read_as_none(tmp_path: Path) -> None:
    assert matrix.read_metrics(tmp_path) is None
    (tmp_path / "metrics.json").write_text("[1]", encoding="utf-8")
    assert matrix.read_metrics(tmp_path) is None
    (tmp_path / "metrics.json").write_text("{bad", encoding="utf-8")
    assert matrix.read_metrics(tmp_path) is None
    (tmp_path / "metrics.json").write_text('{"a": 1}', encoding="utf-8")
    assert matrix.read_metrics(tmp_path) == {"a": 1}


def test_a_process_runs_to_its_exit_code() -> None:
    assert matrix.launch_process([sys.executable, "-c", "raise SystemExit(4)"]) == 4


def test_an_interrupt_goes_on_to_the_child_which_gets_time_to_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    done = tmp_path / "done"
    script = (
        "import signal, sys, time, pathlib\n"
        "def stop(*a):\n"
        f"    pathlib.Path({str(done)!r}).write_text('x'); sys.exit(0)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    ready = tmp_path / "ready.py"
    ready.write_text(script, encoding="utf-8")
    real = subprocess.Popen

    class Watch(real):  # type: ignore[type-arg, misc]
        def wait(self, timeout: float | None = None) -> int:
            if timeout is None and not done.exists():
                time.sleep(0.5)  # let the child install its handler
                raise KeyboardInterrupt
            return super().wait(timeout=timeout)

    monkeypatch.setattr(subprocess, "Popen", Watch)
    with pytest.raises(KeyboardInterrupt):
        matrix.launch_process([sys.executable, str(ready)])
    assert done.exists()


def test_an_interrupted_child_that_will_not_stop_is_terminated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(matrix, "STOP_WAIT_S", 0.5)
    stubborn = "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)"
    real = subprocess.Popen

    class Watch(real):  # type: ignore[type-arg, misc]
        raised = False
        child: "Watch | None" = None

        def wait(self, timeout: float | None = None) -> int:
            Watch.child = self
            if timeout is None and not Watch.raised:
                Watch.raised = True
                time.sleep(0.5)
                raise KeyboardInterrupt
            return super().wait(timeout=timeout)

    monkeypatch.setattr(subprocess, "Popen", Watch)
    with pytest.raises(KeyboardInterrupt):
        matrix.launch_process([sys.executable, "-c", stubborn])
    # the child ignored SIGINT, so the matrix ended it with SIGTERM
    assert Watch.child is not None
    assert Watch.child.returncode == -signal.SIGTERM
