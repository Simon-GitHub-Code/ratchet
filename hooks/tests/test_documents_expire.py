"""``documents.py expire``: what has expired is read from the tree, and deleted with its answers."""

import datetime as dt
from pathlib import Path

import pytest

import documents
from tests.repos import branch, commit, git, repository, write

TODAY = dt.date(2026, 11, 17)
ALPHA, BETA = ".scratch/f/issues/01-alpha.md", ".scratch/f/issues/02-beta.md"
SPEC = ".scratch/f/spec.md"
BRIEF = documents.BRIEF
WORDS = "## In the owner's words"


def ticket(title: str, status: str, extra: str = "") -> str:
    return f"# 01: {title}\n\nStatus: {status}\n{extra}"


def answered(*lines: str) -> str:
    return "\n## Answers\n\n" + "".join(f"{line}\n" for line in lines)


def brief(*lines: str) -> str:
    held = "".join(f"{line}\n" for line in lines)
    return f"# Brief\n\n{WORDS}\n\n<The owner's questions.>\n{held}\n## Purpose\n\nA purpose.\n"


def shut(*extra: tuple[str, str]) -> dict[str, str]:
    return {
        ALPHA: ticket("Alpha", "resolved", answered("- the first")),
        BETA: ticket("Beta", "resolved", answered("- the second")),
        SPEC: "# Spec\n" + answered("- the spec's"),
        **dict(extra),
    }


def test_a_unit_whose_every_ticket_is_resolved_has_expired() -> None:
    found = documents.expired(shut())
    assert found == {".scratch/f": (ALPHA, BETA, SPEC)}


def test_a_unit_with_an_open_ticket_has_not_expired() -> None:
    assert documents.expired({**shut(), BETA: ticket("Beta", "ready-for-agent")}) == {}


def test_a_spec_with_no_ticket_has_not_expired() -> None:
    assert documents.expired({SPEC: "# Spec\n"}) == {}


def test_a_feature_spec_expires_where_its_verdict_record_stands_beside_it() -> None:
    spec, record = "docs/features/f/spec.md", "docs/features/f/verdict.json"
    assert documents.expired({spec: "# Spec\n"}) == {}
    assert documents.expired({spec: "# Spec\n", record: "{}\n"}) == {"docs/features/f": (spec,)}


NAMED = [
    "Parent: ../../f/spec.md\n",
    "Blocked by: ../../f/issues/01-alpha.md\n",
    "Blocked by: 01\n",
    "Blocked by: #1\n",
]


@pytest.mark.parametrize("line", NAMED)
def test_an_open_ticket_elsewhere_that_names_a_file_of_the_unit_holds_it(line: str) -> None:
    other = ".scratch/g/issues/05-other.md"
    assert documents.expired({**shut(), other: ticket("Other", "ready-for-agent", line)}) == {}
    assert ".scratch/f" in documents.expired({**shut(), other: ticket("Other", "ready-for-agent")})


def test_a_closed_ticket_elsewhere_that_names_a_file_of_the_unit_holds_nothing() -> None:
    other = ".scratch/g/issues/05-other.md"
    closed = ticket("Other", "resolved", "Parent: ../../f/spec.md\n")
    assert ".scratch/f" in documents.expired({**shut(), other: closed})


def test_an_open_ticket_in_the_unit_that_names_its_own_files_holds_nothing_else() -> None:
    tree = {**shut(), BETA: ticket("Beta", "ready-for-agent", "Blocked by: 01-alpha.md\n")}
    assert documents.expired(tree) == {}
    assert ".scratch/f" in documents.expired(tree, frozenset({BETA}))


def test_closing_the_last_open_ticket_expires_the_unit() -> None:
    tree = {**shut(), BETA: ticket("Beta", "ready-for-agent")}
    assert documents.closes(tree, BETA)
    assert not documents.closes({**tree, ALPHA: ticket("Alpha", "ready-for-agent")}, BETA)
    assert not documents.closes(tree, "#12")


def test_a_unit_is_left_where_the_head_has_it_expired_and_the_base_has_not() -> None:
    before = {**shut(), BETA: ticket("Beta", "ready-for-agent")}
    assert documents.left(before, shut()) == [".scratch/f"]
    assert documents.left(shut(), shut()) == []
    assert documents.left(before, {}) == []


def test_a_line_is_read_without_its_bullet_and_its_date() -> None:
    assert documents.parts("- a plain line") == (None, "a plain line")
    assert documents.parts("- 2026-01-02: a dated line") == ("2026-01-02", "a dated line")
    assert documents.parts("Q: which way? A: this one") == (None, "Q: which way? A: this one")
    assert documents.parts("- 2026-01-02 no colon") == (None, "2026-01-02 no colon")


def test_the_answers_are_the_lines_under_the_heading_and_blank_lines_are_not() -> None:
    text = "# T\n\n## Answers\n\n- one\n\n  - two\n\n## Next\n\n- not an answer\n"
    assert documents.answers_of(text) == ["- one", "- two"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return repository(tmp_path / "repo", {BRIEF: brief()})


def unit(root: Path, files: dict[str, str] | None = None) -> None:
    for path, text in (files or shut()).items():
        write(root, path, text)
    commit(root, "docs: a unit")


def words(root: Path) -> list[str]:
    text = (root / BRIEF).read_text(encoding="utf-8")
    return [line for line in documents.markdown_text.section(text, WORDS).split("\n") if line]


def test_expire_deletes_the_unit_and_carries_each_answer_dated(root: Path) -> None:
    unit(root)
    done = documents.expire(root, TODAY)
    assert done.deleted == (ALPHA, BETA, SPEC)
    assert done.carried == (
        "- 2026-11-17: the first",
        "- 2026-11-17: the second",
        "- 2026-11-17: the spec's",
    )
    assert not (root / ".scratch").exists()
    assert words(root) == ["<The owner's questions.>", *done.carried]
    assert (root / BRIEF).read_text(encoding="utf-8").endswith("## Purpose\n\nA purpose.\n")


def test_a_second_run_changes_nothing(root: Path) -> None:
    unit(root)
    documents.expire(root, TODAY)
    commit(root, "docs: expired")
    again = documents.expire(root, TODAY)
    assert (again.deleted, again.carried, again.problem) == ((), (), None)
    assert git(root, "status", "--porcelain") == ""


def test_a_line_the_brief_holds_already_is_carried_once_whatever_its_date(root: Path) -> None:
    write(root, BRIEF, brief("- 2026-01-01: the first"))
    unit(root, shut())
    done = documents.expire(root, TODAY)
    assert done.carried == ("- 2026-11-17: the second", "- 2026-11-17: the spec's")
    assert sorted(words(root)).count("- 2026-01-01: the first") == 1


def test_an_answer_with_a_date_keeps_it(root: Path) -> None:
    unit(root, {**shut(), ALPHA: ticket("Alpha", "resolved", answered("- 2026-03-04: dated"))})
    assert "- 2026-03-04: dated" in documents.expire(root, TODAY).carried


def test_a_brief_with_no_place_for_the_lines_stops_the_deletion(root: Path) -> None:
    write(root, BRIEF, "# Brief\n\n## Purpose\n\nA purpose.\n")
    unit(root)
    done = documents.expire(root, TODAY)
    assert done.problem == (
        f"{BRIEF} has no `{WORDS}` section, so nothing expired was deleted: add the heading, then"
        " run `documents.py expire` again"
    )
    assert (root / ALPHA).is_file()
    assert (root / BRIEF).read_text(encoding="utf-8") == "# Brief\n\n## Purpose\n\nA purpose.\n"


def test_no_brief_is_needed_where_a_unit_has_no_answers(tmp_path: Path) -> None:
    bare = repository(tmp_path / "bare", {"README.md": "# A\n"})
    unit(bare, {ALPHA: ticket("Alpha", "resolved")})
    done = documents.expire(bare, TODAY)
    assert (done.deleted, done.carried, done.problem) == ((ALPHA,), (), None)


def test_a_unit_beside_another_leaves_the_other_and_its_folder(root: Path) -> None:
    other = ".scratch/g/issues/01-other.md"
    unit(root, {**shut(), other: ticket("Other", "ready-for-agent")})
    documents.expire(root, TODAY)
    assert (root / other).is_file()
    assert not (root / ".scratch/f").exists()


def test_an_empty_section_takes_the_lines_after_its_heading(root: Path) -> None:
    write(root, BRIEF, f"# Brief\n\n{WORDS}\n\n## Purpose\n\nA purpose.\n")
    unit(root, {ALPHA: ticket("Alpha", "resolved", answered("- one"))})
    documents.expire(root, TODAY)
    assert (root / BRIEF).read_text(encoding="utf-8") == (
        f"# Brief\n\n{WORDS}\n\n- 2026-11-17: one\n\n## Purpose\n\nA purpose.\n"
    )


def test_a_section_followed_at_once_by_a_heading_keeps_a_blank_line_before_it(root: Path) -> None:
    write(root, BRIEF, f"# Brief\n\n{WORDS}\n## Purpose\n\nA purpose.\n")
    unit(root, {ALPHA: ticket("Alpha", "resolved", answered("- one"))})
    documents.expire(root, TODAY)
    assert (root / BRIEF).read_text(encoding="utf-8") == (
        f"# Brief\n\n{WORDS}\n\n- 2026-11-17: one\n\n## Purpose\n\nA purpose.\n"
    )


def test_a_feature_spec_the_branch_adds_is_deleted_before_its_verdict_record(root: Path) -> None:
    spec = "docs/features/f/spec.md"
    branch(root, "feature/1-alpha")
    unit(root, {spec: "# Spec\n" + answered("- from the spec")})
    done = documents.expire(root, TODAY)
    assert done.deleted == (spec,)
    assert done.carried == ("- 2026-11-17: from the spec",)


def test_a_feature_spec_the_default_branch_holds_with_no_record_stays(root: Path) -> None:
    spec = "docs/features/f/spec.md"
    unit(root, {spec: "# Spec\n"})
    assert documents.expire(root, TODAY).deleted == ()


def test_a_repository_with_no_default_branch_commit_adds_no_spec(root: Path) -> None:
    git(root, "branch", "-m", "trunk")
    unit(root, {"docs/features/f/spec.md": "# Spec\n"})
    assert documents.expire(root, TODAY).deleted == ()


def test_the_command_says_what_it_deleted_and_carried(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unit(root)
    assert documents.main(["documents.py", "expire", "--root", str(root)]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[:3] == [f"expire: deleted {path}" for path in (ALPHA, BETA, SPEC)]
    assert printed[3].startswith("expire: carried - ")
    assert printed[3].endswith(f" to {BRIEF}")
    assert len(printed) == 6


def test_the_command_says_when_nothing_has_expired(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert documents.main(["documents.py", "expire", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "expire: nothing has expired\n"


def test_the_command_exits_one_where_the_words_have_no_place(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(root, BRIEF, "# Brief\n")
    unit(root)
    assert documents.main(["documents.py", "expire", "--root", str(root)]) == 1
    assert capsys.readouterr().out.startswith(f"expire: {BRIEF} has no")


BEFORE = brief("- 2026-01-01: kept")
ANSWERS = {"one", "two"}


def test_a_brief_that_only_gains_dated_answers_is_the_carried_lines() -> None:
    after = documents.with_lines(BEFORE, ["- 2026-11-17: one", "- 2026-11-18: two"])
    assert documents.carried(BEFORE, after, ANSWERS)
    assert documents.carried(BEFORE, BEFORE, ANSWERS)


@pytest.mark.parametrize(
    "after",
    [
        BEFORE.replace("A purpose.", "Another purpose."),
        documents.with_lines(BEFORE, ["- 2026-11-17: three"]),
        documents.with_lines(BEFORE, ["- one"]),
        BEFORE.replace("- 2026-01-01: kept\n", ""),
        BEFORE.replace(WORDS, "## Words"),
    ],
    ids=["another section", "another line", "an undated line", "a dropped line", "no section"],
)
def test_a_brief_that_changes_anything_else_is_not(after: str) -> None:
    assert not documents.carried(BEFORE, after, ANSWERS)


def test_a_brief_without_the_section_before_is_not_carried() -> None:
    assert not documents.carried(None, BEFORE, ANSWERS)


CONFLICT = (
    f"# Brief\n\n{WORDS}\n\n<The owner's questions.>\n"
    "<<<<<<< HEAD\n- 2026-03-01: b\n- 2026-01-01: shared\n"
    "=======\n- 2026-02-01: a\n- 2026-11-17: shared\n"
    ">>>>>>> branch\n\n## Purpose\n\nA purpose.\n"
)


def test_a_conflict_in_the_section_keeps_every_line_once_oldest_first() -> None:
    assert documents.settled(CONFLICT) == (
        f"# Brief\n\n{WORDS}\n\n<The owner's questions.>\n- 2026-01-01: shared\n"
        "- 2026-02-01: a\n- 2026-03-01: b\n\n## Purpose\n\nA purpose.\n"
    )


def test_a_conflict_the_base_shows_is_read_without_the_base() -> None:
    shown = CONFLICT.replace("=======\n", "||||||| base\n- 2026-00-00: base\n=======\n", 1)
    assert "base" not in (documents.settled(shown) or "base")
    assert documents.settled(shown) == documents.settled(CONFLICT)


def test_a_conflict_outside_the_section_is_not_settled() -> None:
    outside = CONFLICT.replace("A purpose.", "<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b")
    assert documents.settled(outside) is None
    assert documents.settled("# Brief\n\n## Purpose\n") is None


def test_a_section_that_ends_the_file_is_settled_with_its_final_newline() -> None:
    last = (
        f"# Brief\n\n{WORDS}\n\n<<<<<<< a\n- 2026-01-01: x\n=======\n- 2026-01-02: y\n>>>>>>> b\n"
    )
    assert documents.settled(last) == f"# Brief\n\n{WORDS}\n\n- 2026-01-01: x\n- 2026-01-02: y\n"


def test_a_path_the_reader_cannot_read_is_left_out_of_the_snapshot() -> None:
    held = documents.snapshot([ALPHA, SPEC], lambda path: None if path == SPEC else "# A\n")
    assert held == {ALPHA: "# A\n"}
