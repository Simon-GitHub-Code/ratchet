"""The triggers gate: the first instance of a kind is free, the next one owes a bound safeguard."""

import json
from pathlib import Path

import pytest

import triggers
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]
LATER_CALL = "| survives failing dependencies | later: code:outbound-call | 1 | | `just faults` |"
LATER_BACKUP = "| backup restores | later: life:irreplaceable-data | weekly | | `just restore` |"
TABLE = """
## Triggers

| Kind | Paths | Pattern |
| --- | --- | --- |
| outbound-call | `src/**/*.py` | `requests\\.(get\\|post)\\(` |
| listener | `src/server.py`, `src/worker.py` | |
| stored-format | none: nothing is stored | |
"""


def binding(*rows: str, events: str = "none", table: str = TABLE) -> str:
    return (
        "# Binding\n\n- Application types: service\n"
        f"- Life events: {events}\n\n## Gates\n\n"
        "| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        + "\n".join(["| format | a tool | 0 | | `just fmt` |", *rows])
        + "\n"
        + table
    )


def project(tmp_path: Path, files: dict[str, str] | None = None, *rows: str, **kw: str) -> Path:
    """A repository on ``main`` holding the binding, then a ``change`` branch."""
    root = repository(
        tmp_path,
        {"docs/process/binding.md": binding(*rows, **kw), "README.md": "# A\n", **(files or {})},
    )
    branch(root)
    return root


def run(root: Path, *options: str) -> int:
    return triggers.main(["triggers", "--root", str(root), *options])


CALL = "import requests\nrequests.get('http://x')\n"


def test_the_table_is_read_as_kinds_with_paths_a_pattern_or_a_reason() -> None:
    kinds, problems = triggers.parse(binding())
    assert problems == []
    assert list(kinds) == ["outbound-call", "listener", "stored-format"]
    call = kinds["outbound-call"]
    assert call.paths == ("src/**/*.py",)
    assert call.pattern is not None
    assert call.pattern.search("requests.post(1)")
    assert not call.pattern.search("requests.put(1)")
    assert kinds["listener"].paths == ("src/server.py", "src/worker.py")
    assert kinds["listener"].pattern is None
    assert kinds["stored-format"].never == "nothing is stored"
    assert kinds["stored-format"].paths == ()
    assert triggers.parse("# Binding\n") == ({}, [])


def test_a_row_with_no_paths_a_bad_pattern_or_a_repeated_kind_is_a_problem() -> None:
    table = "\n## Triggers\n\n| Kind | Paths | Pattern |\n|---|---|---|\n| a | | |\n| b | x | ( |\n"
    table += "| c | y |\n| c | z | |\n| d |\n"
    kinds, problems = triggers.parse(binding(table=table))
    assert list(kinds) == ["c"]
    assert [problem.partition(": ")[0] for problem in problems] == [
        "Triggers row 'a' has no Paths",
        "Triggers row 'b' has a Pattern that is not a regex",
        "the Triggers table names 'c' twice",
        "Triggers row 'd' has no Paths",
    ]


def test_instances_are_files_without_a_pattern_and_lines_with_one(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "src/a.py", "x = 1\nrequests.get(1)\ny = 2\nrequests.post(2)\n")
    for name, text in {"server.py": "serve()\n", "data.bin": "x", "b.py": "pass\n"}.items():
        write(root, f"src/{name}", text)
    git(root, "add", "-A")
    found = triggers.instances(root)
    assert found == {
        "outbound-call": ["src/a.py:2", "src/a.py:4"],
        "listener": ["src/server.py"],
        "stored-format": [],
    }
    commit(root, "feat(core): code")
    assert triggers.instances(root, "HEAD") == found
    assert triggers.instances(root, "HEAD~1")["outbound-call"] == []
    assert triggers.instances(root, None, {}) == {}


def test_a_file_that_is_not_text_or_has_gone_is_no_instance(tmp_path: Path) -> None:
    root = project(tmp_path, {"src/a.py": "requests.get(1)\n", "src/gone.py": "requests.get(2)\n"})
    (root / "src/blob.py").write_bytes(b"\xff\xfe requests.get(")
    git(root, "add", "-A")
    commit(root, "feat(core): code")
    (root / "src/gone.py").unlink()
    assert triggers.file_text(root, None, "src/blob.py") == ""
    assert triggers.file_text(root, None, "src/gone.py") == ""
    assert triggers.file_text(root, "HEAD", "src/gone.py").startswith("requests")
    assert triggers.instances(root)["outbound-call"] == ["src/a.py:1"]


def test_a_row_is_owed_when_it_is_later_and_its_kind_is_present_or_its_event_declared(
    tmp_path: Path,
) -> None:
    root = project(
        tmp_path,
        {"src/a.py": CALL},
        LATER_CALL,
        LATER_BACKUP,
        "| telemetry emitted | later: code:entry-point | 1 | | `just t` |",
        "| load | later: life:depended-on | 2 | | `just load` |",
        "| lint | none: not a thing at all, ADR 0001 | 0 | | `just lint` |",
        events="deployed, irreplaceable-data",
    )
    assert triggers.owed(root) == [
        triggers.Owed("survives failing dependencies", "code:outbound-call", "outbound-call", 1),
        triggers.Owed("backup restores", "life:irreplaceable-data", "irreplaceable-data", 0),
    ]
    assert triggers.later_trigger("`later: code:x`") == "code:x"
    assert triggers.later_trigger("a tool") is None


def test_json_prints_the_owed_rows_for_the_route(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, {"src/a.py": CALL * 2}, LATER_CALL)
    assert run(root, "--json") == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "question": "survives failing dependencies",
            "trigger": "code:outbound-call",
            "name": "outbound-call",
            "count": 2,
        }
    ]
    bare = repository(tmp_path / "bare")
    assert run(bare, "--json") == 0
    assert json.loads(capsys.readouterr().out) == []


def test_count_prints_what_each_row_finds(tmp_path: Path, capsys: Out) -> None:
    # Why: whoever writes a Triggers row proves its pattern on the tree before the gate trusts it.
    root = project(tmp_path, {"src/a.py": CALL * 2, "src/server.py": "serve()\n"})
    assert run(root, "--count") == 0
    assert capsys.readouterr().out.splitlines() == [
        "outbound-call: 2 (src/a.py:2, src/a.py:4)",
        "listener: 1 (src/server.py)",
        "stored-format: none: nothing is stored",
    ]
    write(root, "docs/process/binding.md", binding(table=TABLE + "| listener | | |\n"))
    (root / "src/server.py").unlink()
    assert run(root, "--count") == 0
    assert capsys.readouterr().out.splitlines() == [
        "triggers: the Triggers table names 'listener' twice",
        "outbound-call: 2 (src/a.py:2, src/a.py:4)",
        "listener: 0",
        "stored-format: none: nothing is stored",
    ]


def test_the_first_instance_is_free_and_noted(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, None, LATER_CALL)
    write(root, "src/a.py", CALL)
    assert run(root) == 0
    out = capsys.readouterr().out
    assert (
        "note: 'outbound-call' has arrived (src/a.py:2) and owes survives failing dependencies"
        in out
    )
    assert "/ratchet:safeguard" in out
    assert out.endswith("triggers: green against " + git(root, "rev-parse", "HEAD")[:12] + "\n")


def test_a_second_instance_while_a_row_is_owed_is_red(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/b.py", CALL)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert (
        "triggers: 'outbound-call' gained 1 instance(s) (src/b.py:2) while the base already held 1"
        " and these rows are still `later:`: survives failing dependencies. Run /ratchet:safeguard"
        " to bind them before adding more (ADR 0028)"
    ) in out
    assert "triggers: red" in out


def test_a_move_or_a_removal_adds_nothing(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/a.py", "# moved\n" + CALL)
    assert run(root) == 0
    (root / "src/a.py").unlink()
    assert run(root) == 0
    assert "note" not in capsys.readouterr().out


def test_a_change_that_binds_the_row_with_its_second_instance_is_green(tmp_path: Path) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/b.py", CALL)
    text = (root / "docs/process/binding.md").read_text(encoding="utf-8")
    write(root, "docs/process/binding.md", text.replace("later: code:outbound-call", "a timeout"))
    assert run(root) == 0


def test_a_change_cannot_escape_its_judge_by_dropping_the_row_or_the_table(tmp_path: Path) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/b.py", CALL)
    text = (root / "docs/process/binding.md").read_text(encoding="utf-8")
    write(root, "docs/process/binding.md", text.replace(LATER_CALL + "\n", ""))
    assert run(root) == 1
    write(root, "docs/process/binding.md", text.split("## Triggers")[0])
    assert run(root) == 1


def test_a_row_the_base_already_binds_is_not_owed(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"src/a.py": CALL},
        LATER_CALL.replace("later: code:outbound-call", "a timeout"),
    )
    write(root, "src/b.py", CALL)
    assert run(root) == 0


def test_a_kind_without_owed_rows_is_never_red_or_noted(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/server.py", "serve()\n")
    write(root, "src/worker.py", "work()\n")
    assert run(root) == 0
    assert "note" not in capsys.readouterr().out


def test_a_declared_life_event_is_noted_and_never_red(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, None, LATER_BACKUP, events="irreplaceable-data")
    write(root, "src/a.py", CALL)
    assert run(root) == 0
    assert (
        "note: life event 'irreplaceable-data' is declared and owes backup restores;"
        " run /ratchet:safeguard to bind them (ADR 0028)"
    ) in capsys.readouterr().out


def test_an_event_declared_by_the_change_itself_is_judged_from_the_next(tmp_path: Path) -> None:
    root = project(tmp_path, None, LATER_BACKUP)
    text = (root / "docs/process/binding.md").read_text(encoding="utf-8")
    write(
        root, "docs/process/binding.md", text.replace("Life events: none", "Life events: deployed")
    )
    assert run(root) == 0


def test_many_instances_are_listed_few_and_counted(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/b.py", "requests.get(1)\n" * 7)
    assert run(root) == 1
    assert "(src/b.py:1, src/b.py:2, src/b.py:3, src/b.py:4, src/b.py:5 and 2 more)" in (
        capsys.readouterr().out
    )


def test_the_adoption_judges_nothing_and_says_so(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"src/a.py": CALL})
    branch(root)
    write(root, "docs/process/binding.md", binding(LATER_CALL))
    write(root, "src/b.py", CALL)
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "this change is the adoption and nothing is judged" in out
    assert out.count("\n") == 1


def test_a_repository_with_no_commit_to_compare_with_judges_nothing(
    tmp_path: Path, capsys: Out
) -> None:
    tmp_path.joinpath("x").mkdir()
    assert run(tmp_path / "x") == 0
    assert capsys.readouterr().out.startswith("triggers: green: there is no commit")


def test_base_names_the_commit_the_change_is_judged_against(tmp_path: Path) -> None:
    root = project(tmp_path, {"src/a.py": CALL}, LATER_CALL)
    write(root, "src/b.py", CALL)
    commit(root, "feat(core): another call")
    assert run(root, "--base", "main") == 1
    assert run(root, "--base", "HEAD") == 0


LATER_ROLLBACK = (
    "| rolls back safely | later: life:deployed and code:stored-format | 1 | | `just rollback` |"
)
STORED = TABLE.replace(
    "| stored-format | none: nothing is stored | |", "| stored-format | `data/*.db` | |"
)


def test_a_compound_row_is_owed_only_once_every_part_has_fired(tmp_path: Path) -> None:
    files = {"data/a.db": "x"}
    deployed = project(tmp_path / "a", files, LATER_ROLLBACK, events="deployed", table=STORED)
    assert triggers.owed(deployed) == [
        triggers.Owed(
            "rolls back safely", "life:deployed and code:stored-format", "stored-format", 1
        )
    ]
    young = project(tmp_path / "b", files, LATER_ROLLBACK, table=STORED)
    assert triggers.owed(young) == []
    static = project(tmp_path / "c", None, LATER_ROLLBACK, events="deployed", table=STORED)
    assert triggers.owed(static) == []


def test_a_second_stored_format_while_a_compound_row_is_owed_is_red(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, {"data/a.db": "x"}, LATER_ROLLBACK, events="deployed", table=STORED)
    write(root, "data/b.db", "y")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "triggers: 'stored-format' gained 1 instance(s) (data/b.db)" in out
    assert "rolls back safely" in out


def test_a_compound_row_is_noted_by_its_kind_and_never_red_for_its_event(
    tmp_path: Path, capsys: Out
) -> None:
    first = project(tmp_path / "a", None, LATER_ROLLBACK, events="deployed", table=STORED)
    write(first, "data/a.db", "x")
    assert run(first) == 0
    out = capsys.readouterr().out
    assert "note: 'stored-format' has arrived (data/a.db) and owes rolls back safely" in out
    assert "life event" not in out
    held = project(tmp_path / "b", {"data/a.db": "x"}, LATER_ROLLBACK, table=STORED)
    text = (held / "docs/process/binding.md").read_text(encoding="utf-8")
    write(
        held, "docs/process/binding.md", text.replace("Life events: none", "Life events: deployed")
    )
    assert run(held) == 0
    assert "life event" not in capsys.readouterr().out
