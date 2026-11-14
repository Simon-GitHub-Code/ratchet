"""The mutation gate: zero unregistered survivors and no stale register entry, for any tool.

The plugin reads one input (ADR 0050): JSON lines, one object per mutant. A tool's own report is
the project's to convert.
"""

import hashlib
import io
import json
from pathlib import Path

import pytest

import mutation_survivors as gate
from tests.repos import branch, commit, repository, write

Out = pytest.CaptureFixture[str]

# Why: a real mutation tool's run over the crate whose one file is `lib.rs`, converted once to the
# neutral lines; the per-phase logs, which name the machine's paths, are cut from each outcome.
CARGO = Path(__file__).parent / "fixtures/cargo-mutants"
RUST = "src/lib.rs"
UNTESTED_MINUS = "src/lib.rs:BinaryOperator:-@2:11-2:12"

SOURCE_TEXT = (
    "import x from 'y';\n\nexport function add(a, b) {\n  return a + b;\n}\n\n"
    "export const mul = (a, b) => {\n  return a * b;\n};\n"
)


def register(root: Path, *entries: str) -> None:
    write(root, str(gate.REGISTER), "# register\n\n" + "\n\n".join(entries) + "\n")


def entry(tool: str, identity: str, function_hash: str, **overrides: str) -> str:
    fields = {
        "tool": json.dumps(tool),
        "identity": json.dumps(identity),
        "function_hash": json.dumps(function_hash),
        "reason": '"Equivalent: the operands commute in every call site."',
        "date": "2026-11-07",
    }
    fields.update(overrides)
    return "[[survivor]]\n" + "\n".join(f"{k} = {v}" for k, v in fields.items())


def run(root: Path, *flags: str) -> int:
    return gate.main(["gate", "--root", str(root), *flags])


def twin_report(
    root: Path, places: list[tuple[int, int, str]], *, header: str = "", path: str = "src/a.src"
) -> list[gate.Mutant]:
    """Mutants of one mutator and replacement in ``add``, at (line, column, status) of SOURCE_TEXT.

    ``header`` is prepended to the source and every line moves with it.
    """
    moved = header.count("\n")
    lines = [
        json.dumps(
            {
                "file": path,
                "mutator": "ConditionalExpression",
                "replacement": "true",
                "status": status,
                "start": [line + moved, column],
                "end": [line + moved, column + 5],
            }
        )
        for line, column, status in places
    ]
    write(root, path, header + SOURCE_TEXT)
    text = "\n".join(lines) + "\n"
    write(root, "mutants.jsonl", text)
    return gate.neutral_mutants(text, root)[0]


def any_tool(root: Path, *flags: str) -> int:
    return run(root, "--tool", "any-tool", "--mutants", "mutants.jsonl", *flags)


# --- the register ---------------------------------------------------------------------------


def test_malformed_register_entries_are_named(tmp_path: Path) -> None:
    register(
        tmp_path,
        entry("any-tool", "a.src:op:-@1:1-1:2", "sha256:1"),
        entry("any-tool", "a.src:op:+@1:1-1:2", "sha256:1", reason='"short"'),
        entry("other-tool", "a", "md5:1", date='"2026-11-07"'),
        '[[survivor]]\ntool = "any-tool"',
    )
    found, problems = gate.entries(tmp_path)
    assert [e.identity for e in found] == ["a.src:op:-@1:1-1:2"]
    assert problems == [
        "quality/mutation-register.toml: survivor 2: `reason` shorter than 20 characters",
        (
            "quality/mutation-register.toml: survivor 3: "
            "`function_hash` does not start with `sha256:`; `date` missing or not a TOML date"
        ),
        (
            "quality/mutation-register.toml: survivor 4: `identity` missing; "
            "`function_hash` missing; `reason` missing; `date` missing or not a TOML date"
        ),
    ]
    write(tmp_path, str(gate.REGISTER), 'survivor = "not a list"\n')
    assert gate.entries(tmp_path) == ([], [])
    write(tmp_path, str(gate.REGISTER), "survivor = [1]\n")
    assert gate.entries(tmp_path) == (
        [],
        ["quality/mutation-register.toml: survivor 1: not a table"],
    )
    assert gate.entries(tmp_path / "elsewhere") == ([], [])


# --- the unit and the identity ---------------------------------------------------------------


def test_unit_hash_covers_the_enclosing_top_level_declaration() -> None:
    lines = SOURCE_TEXT.splitlines()
    add = gate.unit_hash(lines, 4)
    assert add == gate.unit_hash(lines, 3) == gate.unit_hash(lines, 5)
    assert add != gate.unit_hash(lines, 8)
    assert (
        add
        == "sha256:"
        + hashlib.sha256(b"export function add(a, b) {\n  return a + b;\n}").hexdigest()
    )
    assert gate.unit_hash(lines, 1) == gate.unit_hash(lines, 2), (
        "a blank line belongs to the unit above"
    )
    assert gate.unit_hash(lines, 99) == gate.unit_hash(lines, 9), "past the end is the last unit"
    assert gate.unit_hash([], 1) == "unknown"
    assert gate.unit_bounds(lines, 4) == (2, 5)
    assert gate.unit_bounds(lines, 8) == (6, 9)
    assert gate.unit_bounds([], 1) == (0, 0)
    assert gate.unit_hash(["// comment", "x"], 2) != gate.unit_hash(["// comment"], 1)


def test_the_gate_judges_the_records_of_a_run(tmp_path: Path, capsys: Out) -> None:
    root = tmp_path
    places = [(8, 11, "killed"), (4, 12, "survived"), (8, 13, "uncovered"), (4, 14, "timeout")]
    mutants = twin_report(root, [*places, (8, 15, "skipped")])
    assert any_tool(root) == 1
    out = capsys.readouterr().out
    by_status = {m.status: m for m in mutants}
    survived, uncovered = by_status["survived"], by_status["uncovered"]
    assert survived.identity == "src/a.src:ConditionalExpression:true@2:12-2:17"
    assert uncovered.identity == "src/a.src:ConditionalExpression:true@2:13-2:18"
    assert f"{survived.identity}: survived and not in quality/mutation-register.toml" in out
    assert f"{uncovered.identity}: uncovered and not in" in out
    assert "any-tool: 2/5 killed, 2 live, 0 registered in scope" in out
    register(
        root,
        entry("any-tool", survived.identity, survived.function_hash),
        entry("any-tool", uncovered.identity, uncovered.function_hash),
        entry("any-tool", "src/other.src:X:y@1:1-1:2", "sha256:0"),
    )
    assert any_tool(root) == 0, "an entry for a file outside the run is not judged"
    twin_report(root, [(8, 11, "killed"), (8, 13, "uncovered"), (4, 14, "undecided")])
    assert any_tool(root) == 1
    out = capsys.readouterr().out
    assert f"{survived.identity}: register entry is stale" in out
    assert "ConditionalExpression:true@2:14-2:19: undecided; the run did not decide it" in out


def test_two_survivors_of_one_mutator_in_one_unit_need_two_entries(
    tmp_path: Path, capsys: Out
) -> None:
    root = tmp_path
    first, second = twin_report(root, [(4, 10, "survived"), (4, 22, "survived")])
    assert first.function_hash == second.function_hash
    assert first.identity != second.identity, "where it sits in the unit is part of the identity"
    register(root, entry("any-tool", first.identity, first.function_hash))
    assert any_tool(root) == 1
    out = capsys.readouterr().out
    assert f"{second.identity}: survived and not in quality/mutation-register.toml" in out
    assert f"{first.identity}: survived" not in out
    assert "any-tool: 0/2 killed, 2 live, 1 registered in scope" in out
    register(
        root,
        entry("any-tool", first.identity, first.function_hash),
        entry("any-tool", second.identity, second.function_hash),
    )
    assert any_tool(root) == 0
    third = twin_report(root, [(4, 10, "survived"), (4, 22, "survived"), (4, 30, "survived")])[2]
    assert any_tool(root) == 1
    assert f"{third.identity}: survived and not in" in capsys.readouterr().out


def test_a_survivor_keeps_its_identity_when_the_code_above_its_unit_changes(
    tmp_path: Path, capsys: Out
) -> None:
    root = tmp_path
    (before,) = twin_report(root, [(4, 10, "survived")])
    register(root, entry("any-tool", before.identity, before.function_hash))
    assert any_tool(root) == 0
    (after,) = twin_report(root, [(4, 10, "survived")], header="// one\n// two\n// three\n")
    assert (after.identity, after.function_hash) == (before.identity, before.function_hash)
    assert any_tool(root) == 0, "an edit above the function is not a change to it"
    assert "mutation: green" in capsys.readouterr().out


def test_an_entry_without_a_position_is_red_and_says_how_to_migrate(
    tmp_path: Path, capsys: Out
) -> None:
    root = tmp_path
    first, second = twin_report(root, [(4, 10, "survived"), (4, 22, "survived")])
    old = "src/a.src:ConditionalExpression:true"
    assert first.identity.startswith(f"{old}@")
    register(root, entry("any-tool", old, first.function_hash))
    assert any_tool(root) == 1
    out = capsys.readouterr().out
    assert f"{old}: register entry has the old identity, without a position" in out
    assert f"matched {first.identity}, {second.identity}" in out
    assert f"{first.identity}: survived and not in" in out
    assert f"{second.identity}: survived and not in" in out
    register(root, entry("any-tool", old, "sha256:gone"))
    assert any_tool(root) == 1
    assert "matched nothing live" in capsys.readouterr().out


def test_a_registered_survivor_that_times_out_is_undecided_not_stale(
    tmp_path: Path, capsys: Out
) -> None:
    root = tmp_path
    (survivor,) = twin_report(root, [(4, 10, "survived")])
    register(root, entry("any-tool", survivor.identity, survivor.function_hash))
    twin_report(root, [(4, 10, "timeout")])
    assert any_tool(root) == 1
    out = capsys.readouterr().out
    assert f"{survivor.identity}: timeout; the run could not tell it from a survivor" in out
    assert "rerun it with less load" in out
    assert "stale" not in out
    register(root)
    assert any_tool(root) == 0, "a timeout nobody registered is a kill"


def test_entries_match_survivors_one_to_one(tmp_path: Path, capsys: Out) -> None:
    root = tmp_path
    twin_report(root, [(4, 10, "survived"), (4, 10, "survived")])
    (first, _) = gate.neutral_mutants((root / "mutants.jsonl").read_text(), root)[0]
    register(root, entry("any-tool", first.identity, first.function_hash))
    assert any_tool(root) == 1, "two mutants at one place, one entry"
    assert f"{first.identity}: survived and not in" in capsys.readouterr().out
    register(
        root,
        entry("any-tool", first.identity, first.function_hash),
        entry("any-tool", first.identity, first.function_hash),
    )
    assert any_tool(root) == 0
    twin_report(root, [(4, 10, "survived")])
    assert any_tool(root) == 1, "an entry listed twice"
    assert f"{first.identity}: register entry is stale" in capsys.readouterr().out


# --- the scope of a changed-code run --------------------------------------------------------


def test_changed_scope_without_history_lists_the_tree(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"src/a.src": "x", "b.txt": "y"})
    assert run(root, "--tool", "any-tool", "--changed") == 0
    assert capsys.readouterr().out == "b.txt\nsrc/a.src\n"


# --- any tool, through the tool-neutral input -----------------------------------------------


def cargo_records(*, shift: int = 0, units: bool = True) -> str:
    """The fixture's records, ``shift`` lines down, with or without the unit each names."""
    moved = []
    for line in (CARGO / "mutants.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for key in ("start", "end"):
            record[key][0] += shift
        record["unit"] = [row + shift for row in record["unit"]]
        if not units:
            del record["unit"]
        moved.append(json.dumps(record))
    return "\n".join(moved) + "\n"


def crate(root: Path, *, header: str = "", records: str | None = None) -> list[gate.Mutant]:
    """The fixture crate under ``root`` with its mutants file; the mutants the gate reads."""
    source = (CARGO / "lib.rs").read_text(encoding="utf-8")
    write(root, RUST, header + source)
    text = cargo_records(shift=header.count("\n")) if records is None else records
    write(root, "mutants.jsonl", text)
    return gate.neutral_mutants(text, root)[0]


def cargo(root: Path, *flags: str) -> int:
    return run(root, "--tool", "cargo-mutants", "--mutants", "mutants.jsonl", *flags)


def record(**fields: object) -> str:
    whole: dict[str, object] = {
        "file": RUST,
        "mutator": "BinaryOperator",
        "replacement": "-",
        "status": "survived",
        "start": [18, 11],
        "end": [18, 12],
    }
    return json.dumps({k: v for k, v in (whole | fields).items() if v is not None})


def test_a_real_tools_records_feed_the_gate(tmp_path: Path, capsys: Out) -> None:
    mutants = crate(tmp_path)
    assert cargo(tmp_path) == 1
    out = capsys.readouterr().out
    assert "cargo-mutants: 11/19 killed, 8 live, 0 registered in scope" in out
    assert f"{UNTESTED_MINUS}: survived and not in quality/mutation-register.toml" in out
    assert 'tool = "cargo-mutants"' in out
    live = [m for m in mutants if m.state == "survived"]
    assert len({m.identity for m in live}) == len(live), "no two survivors share an identity"
    register(tmp_path, *(entry("cargo-mutants", m.identity, m.function_hash) for m in live))
    assert cargo(tmp_path) == 0
    assert "8 live, 8 registered in scope" in capsys.readouterr().out


def test_a_survivor_of_any_tool_keeps_its_identity_when_code_above_its_unit_changes(
    tmp_path: Path, capsys: Out
) -> None:
    before = crate(tmp_path)
    register(
        tmp_path,
        *(
            entry("cargo-mutants", m.identity, m.function_hash)
            for m in before
            if m.state in gate.LIVE
        ),
    )
    assert crate(tmp_path, header="// one\n// two\n") == before
    assert cargo(tmp_path) == 0
    source = (tmp_path / RUST).read_text(encoding="utf-8")
    write(tmp_path, RUST, source.replace("value + 1", "1 + value"))
    assert cargo(tmp_path) == 1
    out = capsys.readouterr().out
    assert f"{UNTESTED_MINUS}: survived and not in" in out, "the unit changed, so its hash did"
    assert f"{UNTESTED_MINUS}: register entry is stale" in out


def test_without_a_unit_the_enclosing_top_level_declaration_is_the_unit(tmp_path: Path) -> None:
    with_units = crate(tmp_path)
    assert crate(tmp_path, records=cargo_records(units=False)) == with_units


def test_the_states_of_the_input_are_judged(tmp_path: Path, capsys: Out) -> None:
    states = ["killed", "uncovered", "timeout", "undecided", "skipped"]
    text = "\n\n".join(record(status=state, start=[18, 5 + n]) for n, state in enumerate(states))
    (timed_out,) = [m for m in crate(tmp_path, records=text) if m.state == "timeout"]
    register(tmp_path, entry("cargo-mutants", timed_out.identity, timed_out.function_hash))
    assert cargo(tmp_path) == 1
    out = capsys.readouterr().out
    assert "cargo-mutants: 2/5 killed, 1 live, 1 registered in scope" in out
    assert "src/lib.rs:BinaryOperator:-@2:6-2:12: uncovered and not in" in out
    assert "@2:7-2:12: timeout; the run could not tell it from a survivor" in out
    assert "@2:8-2:12: undecided; the run did not decide it" in out
    assert "@2:9-2:12" not in out, "a skipped mutant is no finding"


def test_an_unviable_mutant_is_counted_apart_and_never_as_a_kill(
    tmp_path: Path, capsys: Out
) -> None:
    states = ["killed", "unviable", "unviable"]
    text = "\n".join(record(status=state, start=[18, 5 + n]) for n, state in enumerate(states))
    crate(tmp_path, records=text)
    assert cargo(tmp_path) == 0
    assert "cargo-mutants: 1/3 killed, 0 live, 0 registered in scope; 2 unviable" in (
        capsys.readouterr().out
    )


def test_a_run_whose_every_mutant_was_unviable_tested_nothing_and_is_red(
    tmp_path: Path, capsys: Out
) -> None:
    states = ["unviable", "unviable", "skipped"]
    text = "\n".join(record(status=state, start=[18, 5 + n]) for n, state in enumerate(states))
    crate(tmp_path, records=text)
    assert cargo(tmp_path) == 1
    out = capsys.readouterr().out
    assert "cargo-mutants: all 2 mutants the run built were unviable, so no test met one" in out
    assert "0/3 killed, 0 live" in out


def test_an_entry_of_any_tool_without_a_position_cannot_say_which_mutant_it_excuses(
    tmp_path: Path, capsys: Out
) -> None:
    (only,) = crate(tmp_path, records=record(end=None))
    assert only.identity == "src/lib.rs:BinaryOperator:-@2:11-2:11", "end defaults to start"
    register(tmp_path, entry("cargo-mutants", "src/lib.rs:BinaryOperator:-", only.function_hash))
    assert cargo(tmp_path) == 1
    assert "register entry has the old identity, without a position" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("not json", "not a JSON object"),
        ("[1]", "not a JSON object"),
        (record(file=None), "`file` is not text"),
        (record(file="", mutator=""), "`file` is empty; `mutator` is empty"),
        (record(replacement=3), "`replacement` is not text"),
        (
            record(status="Survived"),
            (
                "`status` is not one of killed, survived, uncovered, timeout, undecided,"
                " unviable, skipped"
            ),
        ),
        (record(start=None), "`start` and `end` are [line, column], whole numbers, lines from 1"),
        (record(start=[0, 1]), "`start` and `end` are [line, column], whole numbers, lines from 1"),
        (record(end=[18]), "`start` and `end` are [line, column], whole numbers, lines from 1"),
        (
            record(end=[18, True]),
            "`start` and `end` are [line, column], whole numbers, lines from 1",
        ),
        (
            record(unit=[19, 20]),
            "`unit` is the [first, last] line of the declaration the mutant sits in",
        ),
        (
            record(unit="17-19"),
            "`unit` is the [first, last] line of the declaration the mutant sits in",
        ),
        (
            record(start=None, unit=[17, 19]),
            "`start` and `end` are [line, column], whole numbers, lines from 1",
        ),
    ],
)
def test_a_line_that_is_no_mutant_is_named_and_red(
    tmp_path: Path, capsys: Out, *, line: str, problem: str
) -> None:
    crate(tmp_path, records=record(status="killed") + "\n" + line + "\n")
    assert cargo(tmp_path) == 1
    assert f"mutants line 2: {problem}\n" in capsys.readouterr().out


def test_a_replacement_may_be_left_out_and_a_missing_file_has_no_hash(tmp_path: Path) -> None:
    text = record(replacement=None, file="src/gone.rs")
    (only,) = crate(tmp_path, records=text)
    assert (only.identity, only.function_hash) == (
        "src/gone.rs:BinaryOperator:@18:11-18:12",
        "unknown",
    )


def test_a_neutral_run_with_no_mutant_is_red_unless_its_files_had_nothing_to_mutate(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    crate(tmp_path, records="\n")
    assert cargo(tmp_path) == 1
    assert "cargo-mutants: no mutant reported" in capsys.readouterr().out
    assert cargo(tmp_path, "--only", "src/gone.rs") == 1
    assert "no mutant reported" in capsys.readouterr().out
    assert cargo(tmp_path, "--only", f"{RUST},{RUST}") == 0
    assert "cargo-mutants: no mutant in the files it was given" in capsys.readouterr().out
    crate(tmp_path, records="not json\n")
    assert cargo(tmp_path, "--only", RUST) == 1, "a file that could not be read is not an empty run"
    (tmp_path / "mutants.jsonl").unlink()
    assert cargo(tmp_path) == 1
    assert (
        "mutants.jsonl: missing; convert the tool's report to it first" in capsys.readouterr().out
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(record(status="killed")))
    assert run(tmp_path, "--tool", "cargo-mutants", "--mutants", "-") == 0


def test_a_run_without_the_records_is_red(tmp_path: Path, capsys: Out) -> None:
    crate(tmp_path)
    assert run(tmp_path, "--tool", "pitest") == 1
    assert "--mutants is required for pitest: JSON lines" in capsys.readouterr().out


def test_the_changed_scope_of_any_other_tool_is_the_changed_files_that_exist(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(tmp_path, {RUST: "fn a() {}\n", "src/b.rs": "fn b() {}\n"})
    branch(root, "feature/x")
    (root / "src/b.rs").unlink()
    write(root, RUST, "fn a() { }\n")
    write(root, "src/c.rs", "fn c() {}\n")
    commit(root, "refactor: drop b")
    assert run(root, "--tool", "cargo-mutants", "--changed", "--base", "main") == 0
    assert capsys.readouterr().out == "src/c.rs\nsrc/lib.rs\n"


def test_a_changed_scope_with_nothing_changed_prints_nothing(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {RUST: "fn a() {}\n"})
    branch(root, "feature/x")
    assert run(root, "--tool", "cargo-mutants", "--changed", "--base", "main") == 0
    assert capsys.readouterr().out == ""
