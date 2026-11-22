import json
from pathlib import Path

import pytest

import documents
import merge_ready
from tests.repos import branch, commit, git, repository, write
from tests.test_merge_ready import BASE, BUT, STALE, judged, stamp

ALPHA, BETA = ".scratch/f/issues/01-alpha.md", ".scratch/f/issues/02-beta.md"
SPEC = ".scratch/f/spec.md"
BRIEF = documents.BRIEF
WORDS = "## In the owner's words"
LEFT = (
    "expired: .scratch/f has expired and the change leaves it in the tree: run"
    " `documents.py expire` and commit what it deletes"
)
NOT_CARRIED = (
    ": a records change may only append, under `## In the owner's words`, the dated"
    " `## Answers` lines of the files it deletes; this one changes more"
)
NOT_SPENT = (
    "is a ticket the owner agreed to build, and no verdict record of its build is at the merge"
    " base: deleting it is the owner's merge (ADR 0061)"
)
THE_FIRST, THE_SECOND = "- 2026-11-17: Alpha's answer", "- 2026-11-17: Beta's answer"


def ticket(number: int, title: str, status: str, *, labelled: bool = True) -> str:
    label = "Label: ready-for-agent\n" if labelled else ""
    return f"# 0{number}: {title}\n\nStatus: {status}\n{label}\n## Answers\n\n- {title}'s answer\n"


def brief(*lines: str) -> str:
    held = "".join(f"{line}\n" for line in lines)
    return f"# Brief\n\n{WORDS}\n\n<The owner's questions.>\n{held}\n## Purpose\n\nA purpose.\n"


def record_of(slug: str) -> dict[str, str]:
    return {f"docs/features/{slug}/verdict.json": json.dumps({"slug": slug})}


def unit(
    tmp_path: Path, alpha: str = "resolved", beta: str = "ready-for-agent", **extra: str
) -> Path:
    files = {
        **BASE,
        BRIEF: brief(),
        SPEC: "# F\n",
        ALPHA: ticket(1, "Alpha", alpha),
        BETA: ticket(2, "Beta", beta),
        **extra,
    }
    repository(tmp_path, files)
    branch(tmp_path, "feature/2-beta")
    return tmp_path


def test_a_change_that_resolves_the_last_ticket_and_keeps_the_unit_is_red(tmp_path: Path) -> None:
    root = unit(tmp_path)
    write(root, BETA, ticket(2, "Beta", "resolved"))
    commit(root, "feat: the second ticket")
    stamp(root, {**_verdict(), "slug": "beta"})
    assert judged(root) == [LEFT]


def test_the_same_change_with_the_unit_deleted_and_the_answers_in_the_brief_is_green(
    tmp_path: Path,
) -> None:
    root = unit(tmp_path)
    write(root, BETA, ticket(2, "Beta", "resolved"))
    commit(root, "feat: the second ticket")
    write(root, BRIEF, brief(THE_FIRST, THE_SECOND))
    for path in (ALPHA, BETA, SPEC):
        (root / path).unlink()
    commit(root, "docs: expire")
    stamp(root)
    assert judged(root) == []


def test_the_fix_the_judge_names_is_the_command_that_deletes_it(tmp_path: Path) -> None:
    root = unit(tmp_path)
    write(root, BETA, ticket(2, "Beta", "resolved"))
    commit(root, "feat: the second ticket")
    stamp(root, {**_verdict(), "slug": "beta"})
    (reason,) = merge_ready.judge(root, "main", "HEAD")
    assert (reason.code, "run `documents.py expire`" in reason.text) == ("expired", True)


def test_a_unit_already_expired_at_the_merge_base_is_never_red(tmp_path: Path) -> None:
    root = unit(tmp_path, beta="resolved")
    write(root, "src/a.py", "x = 2\n")
    commit(root, "feat: another thing")
    stamp(root)
    assert judged(root) == []


def test_a_feature_spec_left_beside_the_record_that_expires_it_is_red(tmp_path: Path) -> None:
    root = unit(tmp_path)
    write(root, "docs/features/f/spec.md", "# F\n")
    commit(root, "feat: the change, its spec left")
    stamp(root, {**_verdict(), "slug": "f"})
    assert judged(root) == [LEFT.replace(".scratch/f", "docs/features/f")]


def _verdict() -> dict[str, object]:
    return {
        "slug": "thing",
        "ticket": "2",
        "verdict": "approve",
        "unresolved": [],
        "openQuestions": [],
        "assumptions": [],
    }


def spent(tmp_path: Path, *, beta_record: bool = True) -> Path:
    records = {**record_of("alpha"), **(record_of("beta") if beta_record else {})}
    return unit(tmp_path, alpha="resolved", beta="resolved", **records)


def expire(root: Path) -> None:
    write(root, BRIEF, brief(THE_FIRST, THE_SECOND))
    for path in (ALPHA, BETA, SPEC):
        (root / path).unlink()
    commit(root, "docs: expire")


def test_deleting_closed_labelled_tickets_whose_builds_left_records_lands_without_the_owner(
    tmp_path: Path,
) -> None:
    root = spent(tmp_path)
    expire(root)
    assert judged(root) == []


def test_a_ticket_the_owner_finished_with_no_record_of_a_build_waits_for_the_owner(
    tmp_path: Path,
) -> None:
    root = spent(tmp_path, beta_record=False)
    expire(root)
    assert judged(root) == [f"{BUT}{BETA} {NOT_SPENT}; {SPEC} {NOT_SPENT}"]


def test_a_ticket_still_open_is_deleted_only_by_the_owner_when_the_owner_agreed_to_it(
    tmp_path: Path,
) -> None:
    root = unit(tmp_path, **record_of("alpha"))
    expire(root)
    (reason,) = merge_ready.judge(root, "main", "HEAD")
    assert f"{BETA} {NOT_SPENT}" in reason.text


def test_a_ticket_deleted_from_a_unit_that_goes_on_is_a_deleted_record(tmp_path: Path) -> None:
    root = unit(tmp_path, **record_of("alpha"))
    (root / ALPHA).unlink()
    commit(root, "docs: drop one ticket")
    assert judged(root) == [f"{BUT}{ALPHA} deletes a record"]


def test_an_open_ticket_nobody_agreed_to_goes_with_its_unit_in_one_paste(tmp_path: Path) -> None:
    plain = {
        ALPHA: ticket(1, "Alpha", "resolved", labelled=False),
        BETA: ticket(2, "Beta", "ready-for-human", labelled=False),
    }
    root = unit(tmp_path, **plain)
    expire(root)
    assert judged(root) == []


def test_an_unlabelled_ticket_of_an_expired_unit_goes_without_a_record(tmp_path: Path) -> None:
    plain = {
        ALPHA: ticket(1, "Alpha", "resolved", labelled=False),
        BETA: ticket(2, "Beta", "resolved", labelled=False),
    }
    root = unit(tmp_path, **plain)
    expire(root)
    assert judged(root) == []


def test_a_feature_spec_a_record_stands_beside_is_deleted_as_a_record(tmp_path: Path) -> None:
    spec = "docs/features/f/spec.md"
    root = unit(tmp_path, **{spec: "# F\n", **record_of("f")})
    (root / spec).unlink()
    commit(root, "docs: expire")
    assert judged(root) == []


def test_a_feature_spec_only_a_deletion_changes(tmp_path: Path) -> None:
    spec = "docs/features/f/spec.md"
    root = unit(tmp_path, **{spec: "# F\n", **record_of("f")})
    write(root, spec, "# F, rewritten\n")
    commit(root, "docs: rewrite")
    assert judged(root) == [
        f"{BUT}{spec} is a feature spec: a records change may delete it but not edit it"
    ]


def test_the_judge_reads_a_deletion_of_a_file_the_default_branch_changed_since_as_stale(
    tmp_path: Path,
) -> None:
    root = spent(tmp_path)
    git(root, "checkout", "-q", "main")
    write(root, ALPHA, ticket(1, "Alpha", "resolved") + "- 2026-11-18: late\n")
    commit(root, "docs: an answer on main")
    git(root, "checkout", "-q", "feature/2-beta")
    expire(root)
    assert judged(root) == [f"{BUT}{ALPHA} {STALE}"]


def test_a_labelled_ticket_the_spec_informs_holds_the_spec_where_its_build_left_no_record(
    tmp_path: Path,
) -> None:
    root = spent(tmp_path, beta_record=False)
    for path in (ALPHA, BETA, SPEC):
        (root / path).unlink()
    write(root, BRIEF, brief(THE_FIRST, THE_SECOND))
    commit(root, "docs: expire")
    (reason,) = merge_ready.judge(root, "main", "HEAD")
    assert SPEC + " " + NOT_SPENT in reason.text


def test_a_spec_a_parent_line_names_is_held_by_the_ticket_that_names_it(tmp_path: Path) -> None:
    named = ticket(2, "Beta", "resolved") + "Parent: ../spec.md\n"
    root = unit(tmp_path, alpha="resolved", beta="resolved", **record_of("alpha"), **{BETA: named})
    expire(root)
    assert NOT_SPENT in " ".join(map(str, merge_ready.judge(root, "main", "HEAD")))


def test_a_brief_edit_that_adds_any_other_line_is_not_a_record(tmp_path: Path) -> None:
    root = spent(tmp_path)
    expire(root)
    write(root, BRIEF, brief(THE_FIRST, THE_SECOND, "- 2026-11-17: a thing nobody said"))
    commit(root, "docs: and one more")
    assert judged(root) == [f"{BUT}{BRIEF}{NOT_CARRIED}"]


def test_a_brief_edit_that_removes_a_line_is_not_a_record(tmp_path: Path) -> None:
    root = unit(
        tmp_path, alpha="resolved", beta="resolved", **record_of("alpha"), **record_of("beta")
    )
    write(root, BRIEF, brief("- 2026-01-01: held"))
    commit(root, "docs: a line the brief holds")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "feature/2-beta")
    branch(root, "records")
    expire(root)
    write(root, BRIEF, brief(THE_FIRST, THE_SECOND))
    commit(root, "docs: and the held line dropped")
    assert judged(root) == [f"{BUT}{BRIEF}{NOT_CARRIED}"]


def test_the_carried_lines_with_no_deletion_beside_them_are_not_a_record(tmp_path: Path) -> None:
    root = spent(tmp_path)
    write(root, BRIEF, brief(THE_FIRST))
    commit(root, "docs: a line with no deletion")
    assert judged(root) == [f"{BUT}{BRIEF}{NOT_CARRIED}"]


def test_the_work_tree_reads_as_records_only_when_its_brief_edit_is_the_carried_lines(
    tmp_path: Path,
) -> None:
    root = spent(tmp_path)
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "feature/2-beta")
    for path in (ALPHA, BETA, SPEC):
        (root / path).unlink()
    write(root, BRIEF, brief(THE_FIRST, THE_SECOND))
    paths = merge_ready.uncommitted(root)
    assert merge_ready.not_records(root, "main", paths) == []
    write(root, BRIEF, brief(THE_FIRST, "- 2026-11-17: another"))
    assert merge_ready.not_records(root, "main", paths) == [BRIEF]
    (root / BRIEF).unlink()
    assert merge_ready.not_records(root, "main", paths) == [BRIEF]


def test_a_feature_spec_the_work_tree_edits_is_not_records(tmp_path: Path) -> None:
    spec = "docs/features/f/spec.md"
    root = unit(tmp_path, **{spec: "# F\n", **record_of("f")})
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "feature/2-beta")
    write(root, spec, "# F, rewritten\n")
    assert merge_ready.not_records(root, "main", [spec]) == [spec]
    (root / spec).unlink()
    assert merge_ready.not_records(root, "main", [spec]) == []


@pytest.mark.parametrize("head", ["", "no-commit"])
def test_a_work_tree_with_nothing_to_read_holds_no_record(tmp_path: Path, head: str) -> None:
    root = unit(tmp_path)
    paths = [BRIEF] if not head else ["nowhere/a.md"]
    assert merge_ready.not_records(root, "refs/heads/missing", paths) == paths
