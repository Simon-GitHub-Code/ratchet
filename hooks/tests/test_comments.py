import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import comments
import lowered
import owed
import plugin_pin
from tests.repos import branch, commit, git, installed_copy, repository, write
from tests.support import PLUGIN_ROOT

RULE = comments.RULE

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


DOC_WITH_SUPPRESSION = "/// Parses. SILENCE-IT\n"


@pytest.mark.parametrize(("path", "verdict"), [("src/inner.txt", 1), ("api.txt", 0)])
def test_a_doc_comment_is_judged_by_the_published_interface_alone_whatever_its_line_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, path: str, verdict: int
) -> None:
    root = project(tmp_path, LIBRARY_BINDING)
    write(root, path, DOC_WITH_SUPPRESSION)
    output = f"{path}\t1\tdoc\tParses. SILENCE-IT\n"
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


COMMENTS = """\
## Comments

| Files | Comments |
| --- | --- |
| `src/**` | listed |
| `gen/**` | none: generated from the schema by a tool, never by hand |
"""
PIN = f"- Ratchet plugin: 1.2.3 at {'a' * 40}\n"
LISTED = f"# Binding\n\n{PIN}\n{SUPPRESSIONS}\n{COMMENTS}"
KEPT_ROW = "| interpreter | `^RUN-WITH ` | kept |\n"
UNBOUND = 'comments-list *files:\n    @echo "bind: list the comments"; exit 1\n'


def judged(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out) -> list[str]:
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert comments.main(["comments", "--root", str(root), "--files"]) == 0
    return [path for path in capsys.readouterr().out.split("\0") if path]


def changed(root: Path, records: str, monkeypatch: pytest.MonkeyPatch, *more: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(records))
    return comments.main(["comments", "--root", str(root), "--changed", *more])


def test_a_change_judges_the_files_of_listed_rows_it_changes_and_no_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    files = {"src/a.txt": "x\n", "src/kept.txt": "y\n", "gen/b.txt": "z\n"}
    root = project(tmp_path, LISTED, files)
    write(root, "src/a.txt", "x\ny\n")
    write(root, "gen/b.txt", "z // made by the tool\n")
    write(root, "src/new.txt", "w\n")
    write(root, "notes.md", "a page\n")
    assert judged(root, monkeypatch, capsys) == ["src/a.txt", "src/new.txt"]


def test_the_adoption_judges_every_listed_text_file_by_the_binding_it_brings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = repository(
        tmp_path, {"src/old.txt": "x // old\n", "src/blob.bin": "\0\1", "gen/b.txt": "y\n"}
    )
    branch(root)
    write(root, BINDING_PATH, LISTED)
    assert judged(root, monkeypatch, capsys) == ["src/old.txt"]


def test_while_the_lister_is_the_templates_bind_line_nothing_is_judged_and_it_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, LISTED, {"justfile": UNBOUND})
    write(root, "src/a.txt", "x // a reason\n")
    assert judged(root, monkeypatch, capsys) == []
    assert changed(root, "", monkeypatch) == 0
    out = capsys.readouterr().out
    assert "comments: green: the lister is not bound yet, so nothing is judged" in out


def test_a_prose_comment_is_red_and_a_kept_directive_and_a_rises_suppression_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    binding = LISTED.replace("| keep |", f"{KEPT_ROW}| keep |")
    root = project(tmp_path, binding)
    write(
        root, "src/a.txt", "RUN-WITH tool\nx = 1 // SILENCE-IT: misread\ny = 2 // keep it small\n"
    )
    records = (
        "src/a.txt\t1\tcomment\tRUN-WITH tool\n"
        "src/a.txt\t2\tcomment\tSILENCE-IT: misread\n"
        "src/a.txt\t3\tcomment\tkeep it small\n"
    )
    assert changed(root, records, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "src/a.txt:3: a comment no tool reads: 'keep it small'" in out
    assert "src/a.txt:1:" not in out
    assert "src/a.txt:2:" not in out


def test_the_adoption_records_what_exists_and_a_later_comment_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = repository(tmp_path, {"src/old.txt": "x // old reason\n"})
    branch(root)
    write(root, BINDING_PATH, LISTED)
    old = "src/old.txt\t1\tcomment\told reason\n"
    assert changed(root, old, monkeypatch) == 1
    assert changed(root, old, monkeypatch, "--record") == 0
    assert "comments: recorded 1 owed since 1.2.3" in capsys.readouterr().out
    assert owed.read(root).entries == (owed.Entry("src/old.txt", RULE, "old reason", "1.2.3"),)
    assert lowered.owed_grown(root, git(root, "rev-parse", "main")) == []
    commit(root, "chore(method): adopt")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "change")
    branch(root, "later")
    write(root, "src/old.txt", "x // old reason\ny // new reason\n")
    both = f"{old}src/old.txt\t2\tcomment\tnew reason\n"
    assert changed(root, both, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "src/old.txt:2: a comment no tool reads: 'new reason'" in out
    assert "'old reason'" not in out
    assert changed(root, both, monkeypatch, "--record") == 1
    assert "comments: refused: only the adoption and the change that first fills" in (
        capsys.readouterr().out
    )


def test_an_adoption_with_no_comment_records_none_and_writes_no_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = repository(tmp_path, {"src/old.txt": "x\n"})
    branch(root)
    write(root, BINDING_PATH, LISTED)
    assert changed(root, "", monkeypatch, "--record") == 0
    assert "comments: recorded 0 owed since 1.2.3" in capsys.readouterr().out
    assert not (root / owed.LEDGER).exists()


def test_an_adoption_whose_binding_pins_no_plugin_version_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = repository(tmp_path, {"src/old.txt": "x // old reason\n"})
    branch(root)
    write(root, BINDING_PATH, LISTED.replace(PIN, ""))
    old = "src/old.txt\t1\tcomment\told reason\n"
    assert changed(root, old, monkeypatch, "--record") == 1
    assert comments.RECORD_UNPINNED in capsys.readouterr().out
    assert not (root / owed.LEDGER).exists()


def test_a_listed_file_the_change_deletes_is_not_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, LISTED, {"src/gone.txt": "x // old reason\n", "src/kept.txt": "y\n"})
    (root / "src/gone.txt").unlink()
    write(root, "src/kept.txt", "y\nz\n")
    assert judged(root, monkeypatch, capsys) == ["src/kept.txt"]


def test_recording_again_after_the_default_branch_moves_the_pin_owes_the_comments_since_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unlisted = f"# Binding\n\n{PIN}\n{SUPPRESSIONS}"
    root = project(tmp_path, unlisted, {"src/old.txt": "x // old reason\n"})
    write(root, BINDING_PATH, f"{unlisted}\n{COMMENTS}")
    old = "src/old.txt\t1\tcomment\told reason\n"
    assert changed(root, old, monkeypatch, "--record") == 0
    commit(root, "chore(method): fill the lister")
    git(root, "checkout", "-q", "main")
    write(root, BINDING_PATH, unlisted.replace("1.2.3", "1.3.0"))
    commit(root, "chore(method): upgrade the Ratchet plugin")
    git(root, "checkout", "-q", "change")
    git(root, "merge", "-q", "--no-edit", "main")
    base = git(root, "rev-parse", "main")
    assert "the change first fills the lister under the pin 1.3.0" in "".join(
        lowered.owed_grown(root, base)
    )
    assert changed(root, old, monkeypatch, "--record") == 0
    assert owed.read(root).entries == (owed.Entry("src/old.txt", RULE, "old reason", "1.3.0"),)
    assert lowered.owed_grown(root, base) == []
    ledger = (root / owed.LEDGER).read_bytes()
    assert changed(root, old, monkeypatch, "--record") == 0
    assert (root / owed.LEDGER).read_bytes() == ledger, "recording once more changes nothing"


def test_recording_again_after_the_pin_moves_leaves_an_entry_the_lister_does_not_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unlisted = f"# Binding\n\n{PIN}\n{SUPPRESSIONS}"
    root = project(tmp_path, unlisted, {"src/old.txt": "x // old reason\n", "notes.txt": "n\n"})
    write(root, BINDING_PATH, f"{unlisted}\n{COMMENTS}")
    old = "src/old.txt\t1\tcomment\told reason\n"
    assert changed(root, old, monkeypatch, "--record") == 0
    unfound = owed.Entry("notes.txt", RULE, "a comment no lister finds", "1.2.3")
    write(root, str(owed.LEDGER), owed.render([*owed.read(root).entries, unfound]))
    commit(root, "chore(method): fill the lister")
    git(root, "checkout", "-q", "main")
    write(root, BINDING_PATH, unlisted.replace("1.2.3", "1.3.0"))
    commit(root, "chore(method): upgrade the Ratchet plugin")
    git(root, "checkout", "-q", "change")
    git(root, "merge", "-q", "--no-edit", "main")
    assert changed(root, old, monkeypatch, "--record", "--base", "main") == 0
    assert owed.read(root).entries == (
        unfound,
        owed.Entry("src/old.txt", RULE, "old reason", "1.3.0"),
    ), "only what the lister finds is dated with the new pin"
    assert (
        f"adds 'a comment no lister finds' for notes.txt ({RULE}) since '1.2.3'; the change"
        " first fills the lister under the pin 1.3.0"
    ) in "".join(lowered.owed_grown(root, git(root, "rev-parse", "main")))


LISTER = """\
for f in "$@"; do
  awk -v OFS='\\t' -v f="$f" '{ i = index($0, "//"); if (i) { t = substr($0, i + 2); k = "comment";
    if (substr(t, 1, 1) == "/") { k = "doc"; t = substr(t, 2) } sub(/^ +/, "", t);
    print f, FNR, k, t } }' "$f" || exit 1
done
"""


def just_comments(root: Path, plugin: Path, *words: str) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603 # -- a fixed argv; the justfile is the template's own
        [str(shutil.which("just")), "comments", *words],
        cwd=root,
        env={**os.environ, "RATCHET_PLUGIN": str(plugin)},
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, done.stdout + done.stderr


def test_the_templates_recipe_runs_the_projects_lister_from_adoption_to_the_next_change(
    tmp_path: Path,
) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    plugin = installed_copy()
    version = plugin_pin.version_of(PLUGIN_ROOT)
    pin = f"{version} at {plugin_pin.commit_of(plugin) or '1' * 40}"
    binding = LISTED.replace(
        PIN, f"- Ratchet plugin: {pin} tree {plugin_pin.copy_tree_id(plugin)}\n"
    )
    template = (PLUGIN_ROOT / "skills/adopt/templates/justfile").read_text(encoding="utf-8")
    lister = re.sub(
        r'^(comments-list \*files:\n)    @echo "bind:[^\n]*\n',
        r"\1    sh list.sh {{files}}\n",
        template,
        flags=re.MULTILINE,
    )
    assert lister != template
    root = repository(tmp_path, {"src/old.txt": "x // old reason\n", "gen/g.txt": "// made\n"})
    branch(root, "adopt")
    write(root, BINDING_PATH, binding)
    write(root, "justfile", lister)
    write(root, "list.sh", LISTER)
    code, out = just_comments(root, plugin)
    assert code == 1, out
    assert "src/old.txt:1: a comment no tool reads: 'old reason'" in out
    assert "gen/g.txt" not in out
    code, out = just_comments(root, plugin, "--record")
    assert code == 0, out
    assert f"comments: recorded 1 owed since {version}" in out
    commit(root, "chore(method): adopt")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "adopt")
    branch(root, "change")
    write(root, "src/old.txt", "x // old reason\n/// Returns x.\n")
    code, out = just_comments(root, plugin)
    assert code == 1, out
    assert "src/old.txt:2: a doc comment outside the published interface of a library" in out
    assert "'old reason'" not in out
    write(root, "list.sh", "exit 3\n")
    code, out = just_comments(root, plugin)
    assert code != 0, "a lister that fails turns the gate red"


def test_the_version_note_of_a_pinned_workflow_line_is_a_directive_and_other_notes_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path)
    workflow = ".github/workflows/ci.yml"
    pinned = f"      - uses: actions/checkout@{'f' * 40} # v5\n"
    write(root, workflow, f"{pinned}      - run: make # build it first\n")
    output = f"{workflow}\t1\tcomment\tv5\n{workflow}\t2\tcomment\tbuild it first\n"
    assert run(root, output, workflow, monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert f"{workflow}:2: a comment no tool reads: 'build it first'" in out
    assert f"{workflow}:1:" not in out
