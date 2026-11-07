"""The mutation gate: zero unregistered survivors and no stale register entry, for both tools."""

import hashlib
import io
import json
from pathlib import Path

import pytest

import mutation_survivors as gate
from tests.repos import branch, commit, repository, write

Out = pytest.CaptureFixture[str]

SOURCE = (
    "def add(a, b):\n    return a + b\n\n\n"
    "class Calc:\n    def mul(self, a, b):\n        return a * b\n"
)
RESULTS = """    pkg.mod.x_add__mutmut_1: killed
    pkg.mod.x_add__mutmut_2: survived
    pkg.mod.xǁCalcǁmul__mutmut_1: killed
    pkg.mod.xǁCalcǁmul__mutmut_2: no tests
    done
"""
TS_SOURCE = (
    "import x from 'y';\n\nexport function add(a: number, b: number) {\n  return a + b;\n}\n\n"
    "export const mul = (a: number, b: number) => {\n  return a * b;\n};\n"
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


def stryker_report(root: Path, statuses: list[str], path: str = "src/a.ts") -> Path:
    mutants = [
        {
            "id": str(n),
            "mutatorName": "ArithmeticOperator",
            "replacement": "a - b" if n % 2 else "a / b",
            "status": status,
            "location": {"start": {"line": 4 if n % 2 else 8, "column": 10}, "end": {"line": 4}},
        }
        for n, status in enumerate(statuses, 1)
    ]
    report = {"files": {path: {"language": "typescript", "source": TS_SOURCE, "mutants": mutants}}}
    target = root / gate.STRYKER_REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report))
    return target


# --- mutmut ---------------------------------------------------------------------------------


def test_mutmut_names_split_into_module_and_key() -> None:
    assert gate.split_mutant_name("pkg.mod.x_add__mutmut_3") == ("pkg.mod", "x_add")
    assert gate.split_mutant_name("mod.xǁCalcǁmul__mutmut_1") == ("mod", "xǁCalcǁmul")
    assert gate.split_mutant_name("odd.name") == ("odd", "name")
    assert gate._function_and_class("xǁOuter.Innerǁrun") == ("run", ["Outer", "Inner"])  # noqa: SLF001 # -- the split is the unit under test


def test_python_function_hash_follows_the_ast_and_says_unknown(tmp_path: Path) -> None:
    write(tmp_path, "src/pkg/mod.py", SOURCE)
    write(tmp_path, "src/pkg/__init__.py", "")
    first = gate.python_function_hash(tmp_path, "pkg.mod", "x_add")
    assert first.startswith("sha256:")
    write(tmp_path, "src/pkg/mod.py", SOURCE.replace("a + b", "b + a"))
    assert gate.python_function_hash(tmp_path, "pkg.mod", "x_add") != first
    write(tmp_path, "src/pkg/mod.py", SOURCE.replace("a + b", "(a + b)"))
    assert gate.python_function_hash(tmp_path, "pkg.mod", "x_add") == first, (
        "formatting is not a change"
    )
    assert gate.python_function_hash(tmp_path, "pkg.mod", "xǁCalcǁmul").startswith("sha256:")
    assert gate.python_function_hash(tmp_path, "pkg.mod", "xǁCalcǁgone") == "unknown"
    assert gate.python_function_hash(tmp_path, "pkg.mod", "xǁNopeǁmul") == "unknown"
    assert gate.python_function_hash(tmp_path, "pkg.mod", "x_gone") == "unknown"
    assert gate.python_function_hash(tmp_path, "pkg.missing", "x_add") == "unknown"
    assert gate.python_function_hash(tmp_path, "pkg", "x_add") == "unknown", "__init__ has none"
    write(tmp_path, "src/pkg/mod.py", "def (:\n")
    assert gate.python_function_hash(tmp_path, "pkg.mod", "x_add") == "unknown"
    write(tmp_path, "flat.py", SOURCE)
    assert gate.module_file(tmp_path, "flat") == tmp_path / "flat.py"


def test_mutmut_gate_registers_survivors_and_fails_stale_entries(
    tmp_path: Path, capsys: Out
) -> None:
    root = tmp_path
    write(root, "src/pkg/mod.py", SOURCE)
    write(root, "results.txt", RESULTS)
    assert run(root, "--tool", "mutmut", "--results", "results.txt") == 1
    out = capsys.readouterr().out
    assert "pkg.mod.x_add__mutmut_2: survived and not in quality/mutation-register.toml" in out
    assert "pkg.mod.xǁCalcǁmul__mutmut_2: no tests and not in" in out
    assert 'tool = "mutmut"\nidentity = "pkg.mod.x_add__mutmut_2"\nfunction_hash = "sha256:' in out
    assert "mutmut: 2/4 killed, 2 live, 0 registered in scope" in out
    add_hash = gate.python_function_hash(root, "pkg.mod", "x_add")
    mul_hash = gate.python_function_hash(root, "pkg.mod", "xǁCalcǁmul")
    register(
        root,
        entry("mutmut", "pkg.mod.x_add__mutmut_2", add_hash),
        entry("mutmut", "pkg.mod.xǁCalcǁmul__mutmut_2", mul_hash),
    )
    assert run(root, "--tool", "mutmut", "--results", "results.txt") == 0
    assert "mutation: green" in capsys.readouterr().out
    write(root, "src/pkg/mod.py", SOURCE.replace("a * b", "b * a"))
    assert run(root, "--tool", "mutmut", "--results", "results.txt") == 1
    out = capsys.readouterr().out
    assert "pkg.mod.xǁCalcǁmul__mutmut_2: no tests and not in" in out, "the hash moved"
    assert "pkg.mod.xǁCalcǁmul__mutmut_2: register entry is stale" in out
    write(root, "src/pkg/mod.py", SOURCE)
    write(
        root, "results.txt", RESULTS.replace("x_add__mutmut_2: survived", "x_add__mutmut_2: killed")
    )
    assert run(root, "--tool", "mutmut", "--results", "results.txt") == 1
    assert "pkg.mod.x_add__mutmut_2: register entry is stale" in capsys.readouterr().out


def test_mutmut_gate_reads_stdin_and_judges_only_its_scope(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    write(root, "src/pkg/mod.py", SOURCE)
    write(root, "src/pkg/other.py", "def f():\n    return 1\n")
    text = RESULTS.replace("x_add__mutmut_2: survived", "x_add__mutmut_2: killed").replace(
        "xǁCalcǁmul__mutmut_2: no tests", "xǁCalcǁmul__mutmut_2: killed"
    )
    text += "    pkg.other.x_f__mutmut_1: not checked\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert run(root, "--tool", "mutmut", "--results", "-", "--only", "pkg.mod.*") == 0
    assert "mutmut: 4/4 killed" in capsys.readouterr().out
    register(root, entry("mutmut", "pkg.other.x_f__mutmut_1", "sha256:0"))
    write(root, "results.txt", text)
    assert run(root, "--tool", "mutmut", "--results", "results.txt", "--only", "pkg.mod.*") == 0
    assert run(root, "--tool", "mutmut", "--results", "results.txt") == 1
    out = capsys.readouterr().out
    assert "pkg.other.x_f__mutmut_1: not checked; the run did not decide it" in out
    assert "pkg.other.x_f__mutmut_1: register entry is stale" in out


def test_mutmut_gate_needs_results_and_mutants(tmp_path: Path, capsys: Out) -> None:
    assert run(tmp_path, "--tool", "mutmut") == 1
    assert "--results is required" in capsys.readouterr().out
    assert run(tmp_path, "--tool", "mutmut", "--results", "none.txt") == 1
    assert "none.txt: missing" in capsys.readouterr().out
    write(tmp_path, "empty.txt", "nothing here\n")
    assert run(tmp_path, "--tool", "mutmut", "--results", "empty.txt") == 1
    assert "mutmut: no mutant reported" in capsys.readouterr().out


# --- the register ---------------------------------------------------------------------------


def test_malformed_register_entries_are_named(tmp_path: Path) -> None:
    register(
        tmp_path,
        entry("mutmut", "a.x_f__mutmut_1", "sha256:1"),
        entry("mutmut", "a.x_f__mutmut_2", "sha256:1", reason='"short"'),
        entry("pitest", "a", "md5:1", date='"2026-11-07"'),
        '[[survivor]]\ntool = "mutmut"',
    )
    found, problems = gate.entries(tmp_path)
    assert [e.identity for e in found] == ["a.x_f__mutmut_1"]
    assert problems == [
        "quality/mutation-register.toml: survivor 2: `reason` shorter than 20 characters",
        (
            "quality/mutation-register.toml: survivor 3: `tool` is not one of mutmut, stryker; "
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


# --- stryker --------------------------------------------------------------------------------


def test_unit_hash_covers_the_enclosing_top_level_declaration() -> None:
    lines = TS_SOURCE.splitlines()
    add = gate.unit_hash(lines, 4)
    assert add == gate.unit_hash(lines, 3) == gate.unit_hash(lines, 5)
    assert add != gate.unit_hash(lines, 8)
    assert (
        add
        == "sha256:"
        + hashlib.sha256(
            b"export function add(a: number, b: number) {\n  return a + b;\n}"
        ).hexdigest()
    )
    assert gate.unit_hash(lines, 1) == gate.unit_hash(lines, 2), (
        "a blank line belongs to the unit above"
    )
    assert gate.unit_hash(lines, 99) == gate.unit_hash(lines, 9), "past the end is the last unit"
    assert gate.unit_hash([], 1) == "unknown"
    assert gate.unit_hash(["// comment", "x"], 2) != gate.unit_hash(["// comment"], 1)


def test_stryker_gate_judges_the_json_report(tmp_path: Path, capsys: Out) -> None:
    root = tmp_path
    stryker_report(root, ["Killed", "Survived", "NoCoverage", "Timeout", "Ignored"])
    assert run(root, "--tool", "stryker") == 1
    out = capsys.readouterr().out
    assert (
        "src/a.ts:ArithmeticOperator:a / b: Survived and not in quality/mutation-register.toml"
        in out
    )
    assert "src/a.ts:ArithmeticOperator:a - b: NoCoverage and not in" in out
    assert "stryker: 2/5 killed, 2 live, 0 registered in scope" in out
    mutants = gate.stryker_mutants(json.loads((root / gate.STRYKER_REPORT).read_text()))
    by_status = {m.status: m for m in mutants}
    register(
        root,
        entry("stryker", by_status["Survived"].identity, by_status["Survived"].function_hash),
        entry("stryker", by_status["NoCoverage"].identity, by_status["NoCoverage"].function_hash),
        entry("stryker", "src/other.ts:X:y", "sha256:0"),
    )
    assert run(root, "--tool", "stryker") == 0, "an entry for a file outside the run is not judged"
    stryker_report(root, ["Killed", "Killed", "NoCoverage", "Pending", "Ignored"])
    assert run(root, "--tool", "stryker", "--report", str(root / gate.STRYKER_REPORT)) == 1
    out = capsys.readouterr().out
    assert "src/a.ts:ArithmeticOperator:a / b: register entry is stale" in out
    assert "src/a.ts:ArithmeticOperator:a / b: Pending; the run did not decide it" in out


def test_stryker_gate_tolerates_odd_shapes_and_missing_report(tmp_path: Path, capsys: Out) -> None:
    assert run(tmp_path, "--tool", "stryker") == 1
    assert "reports/mutation/mutation.json: missing" in capsys.readouterr().out
    report = {
        "files": {
            "a.ts": {
                "source": "x",
                "mutants": [{"status": "Killed", "location": {"start": {}}}, 3],
            },
            "b.ts": "not a table",
        }
    }
    assert [m.identity for m in gate.stryker_mutants(report)] == ["a.ts:?:"]
    assert gate.stryker_mutants({"files": []}) == []
    assert gate._start_line({"location": {"start": {"line": 0}}}) == 1  # noqa: SLF001 # -- the fallback is the unit under test
    write(tmp_path, str(gate.STRYKER_REPORT), "[]")
    assert run(tmp_path, "--tool", "stryker") == 1
    assert "stryker: no mutant reported" in capsys.readouterr().out


# --- the scope of a changed-code run --------------------------------------------------------


def test_changed_scope_for_both_tools(tmp_path: Path, capsys: Out) -> None:
    root = repository(
        tmp_path,
        {
            "pyproject.toml": (
                '[tool.mutmut]\nsource_paths = ["src"]\ndo_not_mutate = ["src/pkg/skip/*"]\n'
            ),
            "stryker.config.json": json.dumps(
                {"mutate": ["src/**/*.{ts,tsx}", "!src/**/*.test.ts"]}
            ),
            "src/pkg/mod.py": SOURCE,
            "src/a.ts": "x",
        },
    )
    commit(root, "feat(core): second")
    branch(root)
    write(root, "src/pkg/mod.py", SOURCE + "\n")
    write(root, "src/pkg/__init__.py", "")
    write(root, "src/pkg/skip/x.py", "")
    write(root, "tests/test_mod.py", "")
    write(root, "src/a.ts", "y")
    write(root, "src/a.test.ts", "y")
    write(root, "src/b.tsx", "y")
    write(root, "README.md", "# changed\n")
    assert run(root, "--tool", "mutmut", "--changed") == 0
    assert capsys.readouterr().out == "pkg.*\npkg.mod.*\n"
    assert run(root, "--tool", "stryker", "--changed") == 0
    assert capsys.readouterr().out == "src/a.ts,src/b.tsx\n"
    (root / "stryker.config.json").unlink()
    (root / "pyproject.toml").unlink()
    assert gate.stryker_changed(root, None) == ["src/a.ts", "src/b.tsx"]
    assert gate.mutmut_changed(root, None) == ["pkg.*", "pkg.mod.*", "pkg.skip.x.*"]
    assert gate.mutmut_changed(root, "main") == ["pkg.*", "pkg.mod.*", "pkg.skip.x.*"]


def test_changed_scope_without_history_lists_the_tree(tmp_path: Path) -> None:
    root = repository(tmp_path, {"src/a.py": "x = 1\n", "src/a.ts": "x"})
    assert gate.mutmut_changed(root, None) == ["a.*"]
    assert gate.stryker_changed(root, None) == ["src/a.ts"]


def test_mutate_globs_apply_in_order_with_negations() -> None:
    assert gate.expand_braces("src/**/*.{ts,tsx}") == ["src/**/*.ts", "src/**/*.tsx"]
    assert gate.expand_braces("a/{b,c}/{d,e}.ts") == [
        "a/b/d.ts",
        "a/b/e.ts",
        "a/c/d.ts",
        "a/c/e.ts",
    ]
    patterns = ["src/**/*.ts", "!src/**/*.test.ts", "src/keep.test.ts"]
    assert gate.mutated_by("src/x/y.ts", patterns)
    assert not gate.mutated_by("src/x/y.test.ts", patterns)
    assert gate.mutated_by("src/keep.test.ts", patterns)
    assert not gate.mutated_by("lib/y.ts", patterns)
    assert gate.mutated_by("src/main.tsx", gate.DEFAULT_STRYKER_MUTATE)
    assert not gate.mutated_by("src/main.test.tsx", gate.DEFAULT_STRYKER_MUTATE)


def test_a_skeleton_with_nothing_to_mutate_is_not_an_empty_run(tmp_path: Path, capsys: Out) -> None:
    # Why: a fresh adoption's core holds no code, so stryker reports no file and tier 2 stayed
    # red until the first feature, which a red tier 2 on the default branch would block.
    config = '{"mutate": ["src/**/*.ts", "!src/**/index.ts"]}'
    report = '{"schemaVersion": "1", "files": {}}'
    root = repository(
        tmp_path,
        {str(gate.STRYKER_CONFIG): config, "src/core/index.ts": "export {};\n"},
    )
    write(root, str(gate.STRYKER_REPORT), report)
    assert run(root, "--tool", "stryker") == 0
    assert "nothing to mutate yet" in capsys.readouterr().out
    write(root, "src/core/tide.ts", "export const x = 1;\n")
    commit(root, "feat(core): code the globs keep")
    assert run(root, "--tool", "stryker") == 1, "files the globs keep, yet no mutant: misread"
    assert "stryker: no mutant reported" in capsys.readouterr().out


def test_a_changed_file_with_no_mutable_code_is_not_an_empty_run(
    tmp_path: Path, capsys: Out
) -> None:
    # Why: a types-only module has no mutant, and stryker's report is then `{}`, the same as a
    # run that read nothing; the files the run was given tell the two apart.
    config = '{"mutate": ["src/**/*.ts"]}'
    root = repository(
        tmp_path,
        {str(gate.STRYKER_CONFIG): config, "src/core/types.ts": "export type W = 1;\n"},
    )
    write(root, str(gate.STRYKER_REPORT), '{"schemaVersion": "1", "files": {}}')
    assert run(root, "--tool", "stryker") == 1, "a full run still reads {} as empty"
    capsys.readouterr()
    assert run(root, "--tool", "stryker", "--only", "src/core/types.ts") == 0
    assert "no mutant in the files it was given" in capsys.readouterr().out
    assert run(root, "--tool", "stryker", "--only", "src/core/types.ts,src/core/gone.ts") == 1


def test_a_deleted_file_is_not_in_the_changed_scope(tmp_path: Path) -> None:
    config = '{"mutate": ["src/**/*.ts"]}'
    root = repository(
        tmp_path,
        {str(gate.STRYKER_CONFIG): config, "src/a.ts": "a\n", "src/b.ts": "b\n"},
    )
    branch(root, "feature/x")
    (root / "src/a.ts").unlink()
    write(root, "src/b.ts", "b2\n")
    commit(root, "refactor: drop a")
    assert gate.stryker_changed(root, "main") == ["src/b.ts"]


def test_a_full_run_whose_globs_reach_no_tracked_file_is_still_empty(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(
        tmp_path,
        {str(gate.STRYKER_CONFIG): '{"mutate": ["src/**/*.ts"]}', "lib/add.ts": "a + b;\n"},
    )
    write(root, str(gate.STRYKER_REPORT), '{"schemaVersion": "2", "files": {}}')
    assert run(root, "--tool", "stryker") == 1, "a glob that misses the code is no skeleton"
    assert "no mutant reported" in capsys.readouterr().out


def test_an_empty_changed_scope_prints_nothing(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {str(gate.STRYKER_CONFIG): '{"mutate": ["src/**/*.ts"]}'})
    assert run(root, "--tool", "mutmut", "--changed") == 0
    assert run(root, "--tool", "stryker", "--changed") == 0
    assert capsys.readouterr().out == "", "mapfile would read one empty pattern from a newline"
