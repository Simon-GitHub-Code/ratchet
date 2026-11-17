import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import run
from first_hour import matrix, persona
from first_hour import owner as owner_rules
from first_hour.persona import Persona, Slice

PIECE = Slice("html-form", "d", "v", "e")
SHIPPED = ("dotnet-chores", "go-todo-cli", "kotlin-recipes", "ruby-plants", "rust-shortener")

METRICS: dict[str, object] = {
    "meta": {"harness": {"commit": "fa9ee3129eb54ced", "dirty": False}},
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


def recorded(directory: Path, name: str, metrics: dict[str, object] = METRICS) -> Path:
    directory.mkdir(parents=True)
    shaped = {**metrics, "meta": {**dict(metrics.get("meta") or {}), "persona": name}}
    (directory / "metrics.json").write_text(json.dumps(shaped), encoding="utf-8")
    return directory


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


def test_a_runs_command_gives_it_every_toolchain_of_the_matrix(tmp_path: Path) -> None:
    given = (tmp_path / "go", tmp_path / "rust")
    settings = matrix.Settings(tmp_path / "plugin", 90.0, 40.0, "opus", "haiku", given)
    argv = matrix.command(Path("/x/run.py"), settings, persona="go-todo-cli", directory=tmp_path)
    assert argv[-4:] == ["--toolchain", str(given[0]), "--toolchain", str(given[1])]


@pytest.mark.parametrize("subcommand", ["run", "probe", "matrix"])
def test_a_toolchain_that_would_open_the_home_or_the_temp_location_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
    subcommand: str,
) -> None:
    home, temp = tmp_path / "home", tmp_path / "temp"
    (home / ".cargo" / "bin").mkdir(parents=True)
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("tempfile.tempdir", str(temp))
    base = ["run.py", subcommand, "--plugin", "p", "--dir", "d", "--toolchain"]
    for refused in (home, home / ".cargo", tmp_path, temp, tmp_path / "missing"):
        with pytest.raises(SystemExit) as exit_:
            run.main([*base, str(refused)])
        assert exit_.value.code == 2
        assert f"--toolchain: {refused}" in capsys.readouterr().err


def test_a_toolchain_deeper_in_the_home_goes_to_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    given = home / ".rustup" / "toolchains" / "stable"
    given.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    launched: list[list[str]] = []
    monkeypatch.setattr(matrix, "launch_process", lambda argv: launched.append(argv) or 1)
    argv = ["run.py", "matrix", "--plugin", str(tmp_path), "--dir", str(tmp_path / "m")]
    argv += ["--personas", "go-todo-cli,rust-shortener", "--toolchain", str(given)]

    assert run.main(argv) == 0

    assert [a[a.index("--toolchain") :] for a in launched] == [["--toolchain", str(given)]] * 2


def test_a_row_reads_the_measures_of_one_run() -> None:
    cells = matrix.row(owner("go-todo-cli"), METRICS)
    assert len(cells) == len(matrix.HEADER)
    assert cells[:3] == ["go-todo-cli", "Go, a tool", "fa9ee31"]
    assert cells[3] == "usable-merged at 61.5 min"
    assert cells[4:6] == ["41.0", "44.2"]
    assert cells[6:10] == ["9", "5 / 4", "3", "2"]
    assert cells[10] == (
        "asked yes (3 options, recommended yes); record yes; parts 2; design 1/2;"
        " axis F 1 issues, 2 mentions; change questions 0"
    )
    assert cells[11:] == ["2 (Go, pytest)", "yes"]


def test_a_run_with_no_measurements_a_failed_one_and_a_thin_one_each_have_a_row() -> None:
    lost = matrix.row(owner("a", stack=""), None)
    assert lost[:4] == ["a", "unstated", "n/a", "no measurements"]
    assert set(lost[4:]) == {"n/a"}
    assert matrix.row(owner("a"), None, "exit 3")[3] == "exit 3"
    thin = matrix.row(
        owner("a"),
        {"architecture": {"asked": False}, "merges": [], "stack_leaks": {"count": None}},
    )
    assert thin[10] == "asked no; repository not read"
    assert thin[11:] == ["not read", "no"]
    assert thin[9] == "0"
    clean = matrix.row(owner("a"), {**METRICS, "stack_leaks": {"count": 0, "names": []}})
    assert clean[11] == "0"
    pipe = matrix.row(owner("a", stack="a|b"), METRICS)
    assert pipe[1] == "a/b"


@pytest.mark.parametrize(
    ("harness", "cell"),
    [
        ({"commit": "fa9ee3129eb54ced", "dirty": False}, "fa9ee31"),
        ({"commit": "fa9ee3129eb54ced", "dirty": True}, "fa9ee31 (dirty)"),
        (None, "not recorded"),
    ],
)
def test_a_row_names_the_harness_commit_that_drove_its_run(
    harness: dict[str, object] | None, cell: str
) -> None:
    meta = {} if harness is None else {"harness": harness}
    cells = matrix.row(owner("a"), {**METRICS, "meta": meta})
    assert dict(zip(matrix.HEADER, cells, strict=True))["Harness"] == cell


@pytest.mark.parametrize(
    ("toolchains", "cell"),
    [([], "fa9ee31"), (["/a"], "fa9ee31, 1 toolchain"), (["/a", "/b"], "fa9ee31, 2 toolchains")],
)
def test_a_row_counts_the_toolchains_its_run_was_given(toolchains: list[str], cell: str) -> None:
    meta = {"harness": {"commit": "fa9ee3129eb54ced"}, "toolchains": toolchains}
    cells = matrix.row(owner("a"), {**METRICS, "meta": meta})
    assert dict(zip(matrix.HEADER, cells, strict=True))["Harness"] == cell


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
    assert seen[1].count("\n") == 3
    assert [r[0] for r in rows] == ["a", "b", "c"]
    assert rows[1][3] == "no measurements"
    assert rows[2][3] == "exit 7; usable-merged at 61.5 min"
    kept = json.loads((root / "matrix.json").read_text())
    assert set(kept) == {"a", "b", "c"}
    assert kept["b"] is None
    assert (root / "matrix.md").read_text().count("\n") == 5


def test_a_failing_run_still_gets_a_row_with_its_exit_code(tmp_path: Path) -> None:
    rows = matrix.run_all([owner("a")], tmp_path / "m", lambda _p, _d: 3)
    assert rows[0][3] == "exit 3"


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


def wait_for(marker: Path) -> None:
    deadline = time.monotonic() + 60
    while not marker.exists():
        assert time.monotonic() < deadline, f"the child never wrote {marker.name}"
        time.sleep(0.01)


def test_an_interrupt_goes_on_to_the_child_which_gets_time_to_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    done = tmp_path / "done"
    armed = tmp_path / "armed"
    script = (
        "import signal, sys, time, pathlib\n"
        "def stop(*a):\n"
        f"    pathlib.Path({str(done)!r}).write_text('x'); sys.exit(0)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        f"pathlib.Path({str(armed)!r}).write_text('x')\n"
        "time.sleep(60)\n"
    )
    ready = tmp_path / "ready.py"
    ready.write_text(script, encoding="utf-8")
    real = subprocess.Popen

    class Watch(real):  # type: ignore[type-arg, misc]
        def wait(self, timeout: float | None = None) -> int:
            if timeout is None and not done.exists():
                wait_for(armed)
                raise KeyboardInterrupt
            return super().wait(timeout=timeout)

    monkeypatch.setattr(subprocess, "Popen", Watch)
    with pytest.raises(KeyboardInterrupt):
        matrix.launch_process([sys.executable, str(ready)])
    assert done.exists()


def test_an_interrupted_child_that_will_not_stop_is_terminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix, "STOP_WAIT_S", 0.5)
    armed = tmp_path / "armed"
    stubborn = (
        "import signal, time, pathlib; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        f"pathlib.Path({str(armed)!r}).write_text('x'); time.sleep(60)"
    )
    real = subprocess.Popen

    class Watch(real):  # type: ignore[type-arg, misc]
        raised = False
        child: "Watch | None" = None

        def wait(self, timeout: float | None = None) -> int:
            Watch.child = self
            if timeout is None and not Watch.raised:
                Watch.raised = True
                wait_for(armed)
                raise KeyboardInterrupt
            return super().wait(timeout=timeout)

    monkeypatch.setattr(subprocess, "Popen", Watch)
    with pytest.raises(KeyboardInterrupt):
        matrix.launch_process([sys.executable, "-c", stubborn])
    assert Watch.child is not None
    assert Watch.child.returncode == -signal.SIGTERM


def test_table_reads_recorded_runs_into_one_row_each_in_the_order_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    thin = {"stop": {"reason": "wall-clock", "minute": 90.0}, "prompts": {"guard": 2}}
    rust = recorded(tmp_path / "rust.try1" / "rust-shortener", "rust-shortener", thin)
    go = recorded(tmp_path / "go.try2" / "go-todo-cli", "go-todo-cli")
    out = tmp_path / "table"

    assert run.main(["run.py", "table", str(rust), str(go), "--dir", str(out)]) == 0

    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert len(lines) == 4
    assert lines[2].startswith(
        "| rust-shortener | Rust, a web service | not recorded | wall-clock at 90.0 min |"
    )
    assert lines[3].startswith("| go-todo-cli | Go, a command-line tool | fa9ee31 | usable-merged")
    assert (out / "matrix.md").read_text(encoding="utf-8") == printed
    kept = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    assert kept == {
        "rust-shortener": {**thin, "meta": {"persona": "rust-shortener"}},
        "go-todo-cli": {**METRICS, "meta": {**METRICS["meta"], "persona": "go-todo-cli"}},
    }


def test_table_of_a_recorded_run_is_the_table_its_matrix_run_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shaped = {**METRICS, "meta": {**METRICS["meta"], "persona": "go-todo-cli"}}

    def launch(_one: Persona, directory: Path) -> int:
        directory.mkdir(parents=True)
        (directory / "metrics.json").write_text(json.dumps(shaped), encoding="utf-8")
        return 0

    matrix.run_all([persona.load("go-todo-cli")], tmp_path / "m", launch)
    out = tmp_path / "again"

    code = run.main(["run.py", "table", str(tmp_path / "m" / "go-todo-cli"), "--dir", str(out)])

    assert code == 0
    for name in ("matrix.md", "matrix.json"):
        assert (out / name).read_text() == (tmp_path / "m" / name).read_text(), name
    assert capsys.readouterr().out == (tmp_path / "m" / "matrix.md").read_text()


def test_table_refuses_two_runs_of_one_persona_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = recorded(tmp_path / "try1" / "go-todo-cli", "go-todo-cli")
    second = recorded(tmp_path / "try2" / "go-todo-cli", "go-todo-cli")
    out = tmp_path / "table"

    assert run.main(["run.py", "table", str(first), str(second), "--dir", str(out)]) == 2

    seen = capsys.readouterr()
    assert seen.out == ""
    assert str(first) in seen.err
    assert str(second) in seen.err
    assert not out.exists()


@pytest.mark.parametrize(
    ("metrics", "says"),
    [
        (None, "no readable metrics.json"),
        ({"meta": {}}, "names no persona"),
        ({"meta": {"persona": "nobody-here"}}, "nobody-here is not under personas/"),
    ],
)
def test_table_refuses_a_run_it_cannot_name_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, metrics: object, says: str
) -> None:
    good = recorded(tmp_path / "good" / "go-todo-cli", "go-todo-cli")
    bad = tmp_path / "bad"
    bad.mkdir()
    if metrics is not None:
        (bad / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    out = tmp_path / "table"

    assert run.main(["run.py", "table", str(good), str(bad), "--dir", str(out)]) == 2

    seen = capsys.readouterr()
    assert seen.out == ""
    assert f"{bad}: " in seen.err
    assert says in seen.err
    assert not out.exists()


SYNTHETIC: list[dict[str, object]] = [
    {
        "s": 0,
        "kind": "run",
        "persona": "go-todo-cli",
        "harness": {"commit": "abcdef0123456789", "dirty": True},
    },
    {"s": 60, "step": "01", "kind": "question", "class": "product", "channel": "dialog"},
    {"s": 90, "step": "01", "kind": "question", "class": "product", "channel": "prose"},
    {"s": 120, "step": "01", "kind": "question", "class": "default-able", "channel": "dialog"},
    {"s": 300, "kind": "prompt", "tool": "Write", "decision": "allow", "reason": "Ratchet: x"},
    {"s": 310, "kind": "prompt", "tool": "Edit", "decision": "allow", "reason": ""},
    {"s": 320, "kind": "prompt", "tool": "Bash", "decision": "allow", "reason": "no rule"},
    {"s": 600, "kind": "merge", "branch": "chore/init", "how": "hand"},
    {"s": 1806, "kind": "merge", "branch": "feature/1-add", "how": "agent"},
    {"s": 2400, "kind": "merge", "branch": "feature/2-list", "how": "agent"},
    {"s": 2400, "kind": "usable"},
    {
        "s": 2500,
        "kind": "architecture",
        "adr": {"complete": True},
        "binding": {"parts": ["cli", "store"]},
        "features": {"with_design": 1, "specs": 2},
        "reviews": {"results": 3, "design_issues": 1, "mentions": 2},
    },
    {"s": 2600, "kind": "stack_leaks", "count": 1, "names": ["Python"], "items": []},
    {"s": 3000, "kind": "stop", "reason": "usable-merged"},
]


def test_table_shows_the_numbers_the_runs_own_table_shows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "go-todo-cli"
    directory.mkdir()
    (directory / "logs").mkdir()
    log = "".join(json.dumps(e) + "\n" for e in SYNTHETIC)
    (directory / "events.jsonl").write_text(log, encoding="utf-8")
    assert run.main(["run.py", "reduce", str(directory)]) == 0
    measures = dict(
        line[2:-2].split(" | ", 1)
        for line in (directory / "table.md").read_text(encoding="utf-8").splitlines()[2:]
    )
    capsys.readouterr()

    assert run.main(["run.py", "table", str(directory)]) == 0

    cells = dict(
        zip(matrix.HEADER, capsys.readouterr().out.splitlines()[2][2:-2].split(" | "), strict=True)
    )
    hand, merges = measures["Hand merges"].split(" of ")
    assert cells["Stopped"] == measures["Stopped"] == "usable-merged at 50.0 min"
    assert cells["Min to first usable merge"] == "40.0"
    assert measures["Minutes to first usable merged change"] == "40.0"
    assert cells["Usable app merged"] == "yes"
    assert f"{cells['Questions']} ({cells['Product / other']})" == "3 (2 / 1)"
    assert measures["Questions (product / default-able)"] == "3 (2 / 1)"
    assert cells["Guard prompts"] == measures["Guard approval prompts"] == "2"
    assert cells["Automatic merges"] == str(int(merges) - int(hand)) == "2"
    assert (
        cells["Stack leaks"].split()[0]
        == measures["Stack names the plugin introduced (owner never said)"].split()[0]
        == "1"
    )
    architecture = cells["Architecture"]
    assert f"parts {measures['Parts declared in the binding']};" in architecture
    assert f"design {measures['Features with a Design section'].replace(' of ', '/')};" in (
        architecture
    )
    _, issues, mentions = measures["Reviews seen / design-axis issues / axis F mentions"].split(
        " / "
    )
    assert f"axis F {issues} issues, {mentions} mentions;" in architecture
    assert architecture.endswith(
        f"change questions {measures['Architecture-change questions after adoption']}"
    )
    assert cells["Harness"] == "abcdef0 (dirty)"
    assert cells["Min to first feature merge"] == "30.1"
    assert measures["Minutes to first merged change"] == "10.0"
