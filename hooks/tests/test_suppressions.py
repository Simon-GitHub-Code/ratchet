"""Suppressions: the binding lists the directives, and the gate counts what it lists (ADR 0040).

The directives here are made up. The plugin names no tool's directive, so a gate that passes on
``SILENCE-IT`` and ``ONLY-THIS`` shows it reads the table and nothing else.
"""

import re
import shutil
from pathlib import Path

import pytest

import conformance
import markers
import suppressions
from tests.repos import branch, commit, repository, write
from tests.test_conformance import pristine

Out = pytest.CaptureFixture[str]

KINDS = """\
## Suppressions

| Kind | Pattern | Rule |
| --- | --- | --- |
| silence | `SILENCE-IT\\b` | rises |
| quiet | `QUIET\\|HUSH` | rises |
| focus | `ONLY-THIS` | never |

| Question | Kinds |
| --- | --- |
| lint, near-all rules | `silence`, `quiet` |
| unit, property and fitness tests | `focus` |
| strict types | none: this checker has no directive that skips a line |
"""
BINDING = f"# Binding\n\n{KINDS}"


def run(root: Path) -> int:
    return suppressions.main(["gate", "--root", str(root)])


def parsed(text: str = BINDING) -> suppressions.Spec:
    spec = suppressions.parse(text)
    assert spec is not None
    return spec


# --- the section -----------------------------------------------------------------------------


def test_a_binding_without_the_section_has_no_spec() -> None:
    assert suppressions.parse("# Binding\n") is None
    assert suppressions.parse(None) is None


def test_the_section_gives_kinds_with_their_rules_and_the_kinds_that_silence_each_question() -> (
    None
):
    spec = parsed()
    assert spec.problems == ()
    assert [(k.name, k.pattern, k.rule) for k in spec.kinds] == [
        ("silence", "SILENCE-IT\\b", "rises"),
        ("quiet", "QUIET|HUSH", "rises"),
        ("focus", "ONLY-THIS", "never"),
    ]
    assert spec.silenced == {
        "lint, near-all rules": ("silence", "quiet"),
        "unit, property and fitness tests": ("focus",),
        "strict types": (),
    }


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        (
            "| silence | `SILENCE-IT\\b` | rises |",
            "| silence | <`pattern`> | rises |",
            "placeholder",
        ),
        ("| quiet |", "| silence |", "'silence' is listed twice"),
        ("`(`" if False else "`ONLY-THIS`", "`(`", "needs one backticked extended regex"),
        ("`ONLY-THIS`", "ONLY-THIS", "needs one backticked extended regex"),
        ("`ONLY-THIS` | never", "`ONLY-THIS` | sometimes", "write `rises` or `never`"),
        ("`silence`, `quiet`", "`silence`, `ghost`", "names the kind 'ghost', which has no row"),
        ("`silence`, `quiet`", "silence", "names no kind"),
        ("none: this checker has no directive that skips a line", "none: x", "says none with no"),
        ("| strict types | none", "| <question> | none", "still has the template's placeholder"),
        ("| Kind | Pattern | Rule |", "| Name | Pattern | Rule |", "lacks its table"),
        ("| Question | Kinds |", "| Question | Rows |", "lacks its table"),
    ],
)
def test_a_section_that_is_unfilled_or_malformed_says_what_is_wrong(
    old: str, new: str, problem: str
) -> None:
    assert old in BINDING, old
    spec = parsed(BINDING.replace(old, new, 1))
    assert any(problem in line for line in spec.problems), spec.problems


def test_a_pattern_is_valid_when_the_search_command_takes_it() -> None:
    assert suppressions.valid("a|b")
    assert not suppressions.valid("(")


# --- counting -------------------------------------------------------------------------------


def project(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    root = repository(tmp_path, {"docs/process/binding.md": BINDING, **(files or {})})
    commit(root, "feat(core): second")
    branch(root)
    return root


def test_a_kind_that_rises_is_red_and_one_that_is_paid_down_is_green(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, {"a.txt": "x SILENCE-IT\nQUIET\n"})
    assert run(root) == 0
    write(root, "a.txt", "x SILENCE-IT\nQUIET\nHUSH\n")
    assert run(root) == 1
    assert "quiet: rose from 1 to 2" in capsys.readouterr().out
    write(root, "a.txt", "x SILENCE-IT\n")
    assert run(root) == 0


def test_an_untracked_file_counts_and_a_longer_word_does_not_match_a_bounded_pattern(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "new.txt", "SILENCE-ITS\n")
    assert run(root) == 0
    write(root, "new.txt", "SILENCE-IT\n")
    assert run(root) == 1
    assert "silence: rose from 0 to 1" in capsys.readouterr().out


def test_a_never_kind_fails_wherever_it_matches_and_names_the_line(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "t.txt", "one\nONLY-THIS here\n")
    assert run(root) == 1
    assert "focus at t.txt:2:ONLY-THIS here" in capsys.readouterr().out


def test_the_binding_that_quotes_every_pattern_is_not_searched(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert suppressions.counts(root, None, parsed().kinds) == {"silence": 0, "quiet": 0}
    assert suppressions.focused(root, parsed().kinds) == []


def test_a_change_cannot_loosen_its_own_judge(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    write(root, "docs/process/binding.md", BINDING.replace("| focus | `ONLY-THIS` | never |\n", ""))
    write(root, "a.txt", "ONLY-THIS and SILENCE-IT\n")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "focus at a.txt:1" in out, "a never kind the head dropped is still held"
    assert "silence: rose from 0 to 1" in out


def test_a_change_that_drops_a_rises_kind_from_the_table_still_has_it_counted(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    write(
        root,
        "docs/process/binding.md",
        BINDING.replace("| silence | `SILENCE-IT\\b` | rises |\n", ""),
    )
    write(root, "a.txt", "SILENCE-IT\n")
    assert run(root) == 1
    assert "silence: rose from 0 to 1" in capsys.readouterr().out


def test_a_merge_base_with_no_section_counts_nothing_and_says_so(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(tmp_path, {"docs/process/binding.md": "# Binding\n"})
    commit(root, "feat(core): second")
    branch(root)
    write(root, "docs/process/binding.md", BINDING)
    write(root, "a.txt", "SILENCE-IT\n")
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "the merge base's binding has no Suppressions section: none counted" in out


def test_a_repository_with_no_base_yet_counts_nothing(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"docs/process/binding.md": BINDING, "a.txt": "SILENCE-IT\n"})
    assert run(root) == 0
    assert capsys.readouterr().out == "suppressions: green\n"


def test_nowhere_to_look_is_not_a_finding(tmp_path: Path) -> None:
    assert suppressions.focused(tmp_path / "nowhere", parsed().kinds) == []


def test_the_search_the_markers_gate_shares_can_leave_a_path_out(tmp_path: Path) -> None:
    root = repository(tmp_path, {"a.txt": "TODO\n", "skip.txt": "TODO\n"})
    assert markers.grep_count(root, "TODO", None) == 2
    assert markers.grep_count(root, "TODO", None, outside="skip.txt") == 1


# --- conformance -----------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def found(root: Path) -> list[str]:
    return [
        f.problem for f in conformance.findings(root) if f.stops == conformance.SUPPRESSIONS_STOPS
    ]


def rebind(root: Path, old: str, new: str) -> None:
    path = root / "docs/process/binding.md"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_adopted_template_with_its_section_filled_is_green(repo: Path) -> None:
    assert found(repo) == []


def test_a_bound_gate_the_directives_can_silence_must_say_which_kinds(repo: Path) -> None:
    row = f"| strict types | `{conformance.SELF_TEST_KIND}` |\n"
    rebind(repo, row, "")
    [problem] = found(repo)
    assert "gate 'strict types' is bound to a tool" in problem


def test_a_gate_with_no_tool_bound_needs_no_line(repo: Path) -> None:
    path = repo / "docs/process/binding.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(
            r"^(\| strict types \| )[^|]*(\| 0 \|)",
            r"\1later: not yet \2",
            text,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    row = f"| strict types | `{conformance.SELF_TEST_KIND}` |\n"
    rebind(repo, row, "")
    assert found(repo) == []


def test_a_binding_without_the_section_is_given_the_templates(repo: Path) -> None:
    rebind(repo, "## Suppressions", "## Silenced")
    [problem] = found(repo)
    assert "has no '## Suppressions' section" in problem


def test_a_malformed_kind_is_red(repo: Path) -> None:
    rebind(
        repo,
        f"| {conformance.SELF_TEST_KIND} | `self-test: silence` | rises |",
        "| k | `(` | rises |",
    )
    assert any("needs one backticked extended regex" in p for p in found(repo))
