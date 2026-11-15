import io
from pathlib import Path

import pytest

import comments
import owed
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]

SUPPRESSIONS = """\
## Suppressions

| Kind | Pattern | Rule |
| --- | --- | --- |
| silence | `SILENCE-IT\\b` | rises |
| keep | `KEEP-[[:digit:]]+` | rises |

| Question | Kinds |
| --- | --- |
| lint, near-all rules | `silence` |
"""
LIBRARY = """\
- Application types: library, cli

## Architecture

- Published interface: `lib/**`, `api.txt`
"""
BINDING = f"# Binding\n\n{SUPPRESSIONS}"
LIBRARY_BINDING = f"# Binding\n\n{LIBRARY}\n{SUPPRESSIONS}"
BINDING_PATH = "docs/process/binding.md"


def project(tmp_path: Path, binding: str = BINDING, files: dict[str, str] | None = None) -> Path:
    root = repository(tmp_path, {BINDING_PATH: binding, **(files or {})})
    commit(root, "feat(core): second")
    branch(root)
    return root


def run(root: Path, records: str, *paths: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(records))
    return comments.main(["comments", "--root", str(root), *paths])


def owing(root: Path, *entries: tuple[str, str]) -> None:
    write(
        root,
        str(owed.LEDGER),
        owed.render([owed.Entry(path, comments.RULE, text, "0.13.0") for path, text in entries]),
    )


def test_a_comment_no_tool_reads_is_red_and_named_by_path_line_and_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "x = 1\nx = 2 // keep it small\n")
    assert run(root, "a.txt\t2\tcomment\tkeep it small\n", "a.txt", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert "a.txt:2: a comment no tool reads: 'keep it small'" in out
    assert out.endswith(f"comments: red against {git(root, 'rev-parse', 'main')[:12]}\n")


def test_files_the_lister_finds_no_comment_in_are_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, files={"a.txt": "x = 1\n"})
    assert run(root, "", "a.txt", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out.startswith("comments: green")


def test_a_record_whose_text_its_line_does_not_show_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "x = 1 // SILENCE-IT\ny = 2\n")
    assert run(root, "a.txt\t2\tcomment\tSILENCE-IT\n", "a.txt", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert "comments: refused: the lister reports 'SILENCE-IT' at a.txt:2" in out
    assert "which that line does not show" in out


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        ("a.txt\t1\tcomment\n", "is not a record of path, line, kind and text"),
        ("a.txt\tone\tcomment\tnote\n", "is not a record of path, line, kind and text"),
        ("a.txt\t1\tremark\tnote\n", "is not a record of path, line, kind and text"),
        ("b.txt\t1\tcomment\tnote\n", "reports b.txt, a file it was not given"),
        ("a.txt\t0\tcomment\tnote\n", "reports line 0 of a.txt, which has 2"),
        ("a.txt\t3\tcomment\t\n", "reports line 3 of a.txt, which has 2"),
        ("a.txt\t1\tcomment\tnote\ndata.bin\t1\tcomment\tnote\n", "data.bin, which is not a text"),
    ],
)
def test_lister_output_that_is_not_a_record_of_a_given_text_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, capsys: Out, output: str, reason: str
) -> None:
    root = project(tmp_path, files={"a.txt": "x // note\n// more\n", "data.bin": "\0note\n"})
    assert run(root, output, "a.txt", "data.bin", monkeypatch=monkeypatch) == 1
    assert reason in capsys.readouterr().out


def test_a_refused_output_judges_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, files={"a.txt": "x // note\n"})
    output = "a.txt\t1\tcomment\tnote\na.txt\t1\tcomment\tghost\n"
    assert run(root, output, "a.txt", monkeypatch=monkeypatch) == 1
    assert "a comment no tool reads" not in capsys.readouterr().out


def test_a_comment_on_a_line_a_suppressions_pattern_matches_is_a_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "x = 1 // SILENCE-IT: the rule misreads this\n")
    output = "a.txt\t1\tcomment\tSILENCE-IT: the rule misreads this\n"
    assert run(root, output, "a.txt", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out.startswith("comments: green")


def test_patterns_are_matched_by_the_search_the_suppressions_gate_counts_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "// KEEP-42\n// SILENCE-ITS own\n")
    output = "a.txt\t1\tcomment\tKEEP-42\na.txt\t2\tcomment\tSILENCE-ITS own\n"
    assert run(root, output, "a.txt", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert "KEEP-42" not in out, "a POSIX class reads as the search command reads it"
    assert "a.txt:2: a comment no tool reads: 'SILENCE-ITS own'" in out


def test_a_directive_on_another_line_does_not_excuse_a_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "x // SILENCE-IT\n// a reason\n")
    assert run(root, "a.txt\t2\tcomment\ta reason\n", "a.txt", monkeypatch=monkeypatch) == 1
    assert "a.txt:2: a comment no tool reads: 'a reason'" in capsys.readouterr().out


def test_a_path_with_glob_characters_is_searched_as_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    write(root, "f[1].txt", "// SILENCE-IT\n")
    write(root, "f1.txt", "// a note\n")
    assert run(root, "f1.txt\t1\tcomment\ta note\n", "f[1].txt", "f1.txt", monkeypatch=monkeypatch)
    assert not run(root, "f[1].txt\t1\tcomment\tSILENCE-IT\n", "f[1].txt", monkeypatch=monkeypatch)


@pytest.mark.parametrize(
    ("binding", "record", "verdict"),
    [
        (LIBRARY_BINDING, ("lib/core/api.txt", "doc"), 0),
        (LIBRARY_BINDING, ("api.txt", "doc"), 0),
        (LIBRARY_BINDING, ("src/inner.txt", "doc"), 1),
        (LIBRARY_BINDING, ("lib/core/api.txt", "comment"), 1),
        (LIBRARY_BINDING.replace("library, ", ""), ("lib/core/api.txt", "doc"), 1),
        (
            LIBRARY_BINDING.replace("`lib/**`, `api.txt`", "none: not a library"),
            ("api.txt", "doc"),
            1,
        ),
        (BINDING, ("api.txt", "doc"), 1),
    ],
)
def test_a_doc_comment_is_allowed_only_in_the_published_interface_of_a_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    binding: str,
    record: tuple[str, str],
    verdict: int,
) -> None:
    root = project(tmp_path, binding)
    path, kind = record
    write(root, path, "/// Returns the parsed value.\n")
    output = f"{path}\t1\t{kind}\tReturns the parsed value.\n"
    assert run(root, output, path, monkeypatch=monkeypatch) == verdict


def test_a_doc_comment_outside_the_interface_is_named_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, LIBRARY_BINDING)
    write(root, "src/inner.txt", "/// Inner.\n")
    assert run(root, "src/inner.txt\t1\tdoc\tInner.\n", "src/inner.txt", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "src/inner.txt:1: a doc comment outside the published interface of a library" in out


@pytest.mark.parametrize(
    "widened",
    [
        LIBRARY_BINDING,
        BINDING.replace("| keep |", "| reason | `a reason` | rises |\n| keep |"),
    ],
)
def test_the_merge_bases_binding_judges_so_a_change_cannot_excuse_its_own_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, widened: str
) -> None:
    root = project(tmp_path)
    write(root, BINDING_PATH, widened)
    write(root, "api.txt", "/// a reason\n")
    assert run(root, "api.txt\t1\tdoc\ta reason\n", "api.txt", monkeypatch=monkeypatch) == 1


def test_with_no_merge_base_the_work_trees_binding_judges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = repository(tmp_path, {BINDING_PATH: LIBRARY_BINDING, "api.txt": "/// Parses.\n"})
    assert run(root, "api.txt\t1\tdoc\tParses.\n", "api.txt", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out == "comments: green\n"


def test_the_adoption_is_judged_by_the_binding_it_adds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    commit(root, "feat(core): second")
    branch(root)
    write(root, BINDING_PATH, LIBRARY_BINDING)
    write(root, "api.txt", "/// Parses.\n")
    assert run(root, "api.txt\t1\tdoc\tParses.\n", "api.txt", monkeypatch=monkeypatch) == 0


def test_a_binding_without_suppressions_makes_no_line_a_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, "# Binding\n")
    write(root, "a.txt", "// SILENCE-IT\n")
    assert run(root, "a.txt\t1\tcomment\tSILENCE-IT\n", "a.txt", monkeypatch=monkeypatch) == 1


def test_an_owed_entry_excuses_its_comment_and_the_gate_says_how_many_are_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "// old reason\nx = 1\n")
    owing(root, ("a.txt", "old reason"))
    assert run(root, "a.txt\t1\tcomment\told reason\n", "a.txt", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out.startswith(f"comments: 1 owed in {owed.LEDGER}\n")


def test_an_owed_comment_moved_within_its_file_keeps_its_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "x = 1\ny = 2\n    //   old reason  \n")
    owing(root, ("a.txt", "old reason"))
    output = "a.txt\t3\tcomment\t  old reason  \n"
    assert run(root, output, "a.txt", monkeypatch=monkeypatch) == 0


def test_a_reworded_owed_comment_is_red_and_its_entry_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "// old reason, reworded\n")
    owing(root, ("a.txt", "old reason"))
    output = "a.txt\t1\tcomment\told reason, reworded\n"
    assert run(root, output, "a.txt", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert "a.txt:1: a comment no tool reads: 'old reason, reworded'" in out
    assert "an entry owes the comment 'old reason' in a.txt, which is no longer found" in out


def test_a_copied_owed_comment_is_red_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "// old reason\n// old reason\n")
    owing(root, ("a.txt", "old reason"))
    output = "a.txt\t1\tcomment\told reason\na.txt\t2\tcomment\told reason\n"
    assert run(root, output, "a.txt", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert out.count("a comment no tool reads") == 1
    assert "1 owed" in out


def test_an_entry_for_another_file_or_rule_excuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, files={"b.txt": "// old reason\n"})
    write(root, "a.txt", "// old reason\n")
    write(
        root,
        str(owed.LEDGER),
        owed.render(
            [
                owed.Entry("b.txt", comments.RULE, "old reason", "0.13.0"),
                owed.Entry("a.txt", "markers", "old reason", "0.13.0"),
            ]
        ),
    )
    assert run(root, "a.txt\t1\tcomment\told reason\n", "a.txt", monkeypatch=monkeypatch) == 1
    capsys.readouterr()
    assert run(root, "", "b.txt", "a.txt", monkeypatch=monkeypatch) == 1
    stale = [line for line in capsys.readouterr().out.splitlines() if "owes the comment" in line]
    assert stale == [
        (
            f"{owed.LEDGER}: an entry owes the comment 'old reason' in b.txt, which is no longer"
            " found; drop it"
        )
    ]


@pytest.mark.parametrize("judged", [("a.txt",), ()])
def test_an_entry_whose_file_is_judged_or_gone_and_whose_comment_is_not_found_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, capsys: Out, judged: tuple[str, ...]
) -> None:
    root = project(tmp_path, files={"a.txt": "// gone\n// gone\n"})
    owing(root, ("a.txt", "gone"), ("a.txt", "gone"))
    if judged:
        write(root, "a.txt", "x = 1\n")
    else:
        (root / "a.txt").unlink()
    assert run(root, "", *judged, monkeypatch=monkeypatch) == 1
    assert (
        "owes the comment 'gone' in a.txt (2 entries), which is no longer"
        in capsys.readouterr().out
    )


def test_an_entry_for_a_file_that_is_not_judged_is_left_to_the_run_that_judges_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, files={"a.txt": "// owed\n"})
    owing(root, ("a.txt", "owed"))
    assert run(root, "", monkeypatch=monkeypatch) == 0


def test_a_ledger_that_does_not_read_is_named_and_excuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "// owed\n")
    write(root, str(owed.LEDGER), "[[owed]\n")
    assert run(root, "a.txt\t1\tcomment\towed\n", "a.txt", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert f"{owed.LEDGER}: does not parse" in out
    assert "a.txt:1: a comment no tool reads" in out


def test_a_comment_with_no_text_cannot_be_owed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "a.txt", "//\n")
    assert run(root, "a.txt\t1\tcomment\t\n", "a.txt", monkeypatch=monkeypatch) == 1
    assert "a.txt:1: a comment no tool reads: ''" in capsys.readouterr().out
    write(root, str(owed.LEDGER), owed.render([owed.Entry("a.txt", comments.RULE, "", "0.13.0")]))
    assert run(root, "a.txt\t1\tcomment\t\n", "a.txt", monkeypatch=monkeypatch) == 1
