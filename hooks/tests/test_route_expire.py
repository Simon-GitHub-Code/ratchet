from pathlib import Path

import documents
import route
from tests.repos import commit, git, repository, write
from tests.test_route import ADOPTED, CONJECTURE, MANIFEST, hooks_are_installed

__all__ = ["hooks_are_installed"]

ONE = ".scratch/f/issues/01-a.md"
TWO = ".scratch/f/issues/02-b.md"
OTHER = ".scratch/g/issues/01-c.md"
SPEC = ".scratch/f/spec.md"
RESOLVED = "# 01: A\n\nStatus: resolved\n\n## Answers\n\n- the first answer\n"
BRIEF = f"# Brief\n\n{documents.OWNERS_WORDS}\n\n## Purpose\n\nA purpose.\n"
THEN = f", then run `{route.EXPIRE}`"
PLUGIN_FORM = "python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "


def unit(tmp_path: Path, **extra: str) -> Path:
    files = {**ADOPTED, "docs/design-brief.md": BRIEF, SPEC: "# F\n", **extra}
    return repository(tmp_path, files)


def test_a_blocker_an_open_ticket_of_another_unit_names_keeps_its_unit_and_names_no_deleter(
    tmp_path: Path,
) -> None:
    waiting = (
        "# 01: C\n\nBlocked by: ../../f/issues/01-a.md\n\nStatus: ready-for-agent" + CONJECTURE
    )
    root = unit(
        tmp_path, **{ONE: "# 01: A\n\nStatus: ready-for-human\n", OTHER: waiting, TWO: RESOLVED}
    )
    held = route.decide(root, None)
    assert held.stage == "blocked"
    assert THEN not in held.command


def test_a_blocker_that_is_not_the_last_open_ticket_names_no_deleter(tmp_path: Path) -> None:
    files = {
        ONE: "# 01: A\n\nStatus: ready-for-human\n",
        TWO: "# 02: B\n\nBlocked by: 01\n\nStatus: ready-for-agent" + CONJECTURE,
        ".scratch/f/issues/03-c.md": "# 03: C\n\nStatus: ready-for-human\n",
    }
    held = route.decide(unit(tmp_path, **files), None)
    assert held.stage == "blocked"
    assert THEN not in held.command


def test_a_merged_branch_whose_ticket_is_the_last_names_the_deleter_with_the_mark(
    tmp_path: Path,
) -> None:
    root = unit(tmp_path, **{ONE: "# 01: A\n\nStatus: ready-for-agent" + CONJECTURE})
    git(root, "switch", "-q", "-c", "feature/1-a")
    write(root, "src/a.py", "a = 1\n")
    commit(root, "feat(a): a")
    git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "feature/1-a", "-m", "Merge branch 'feature/1-a'")
    done = route.decide(root, None)
    assert done.stage == "resolve"
    assert done.command == (
        f"mark ticket {ONE} resolved (`Status: resolved` on a local ticket; close the issue on a"
        f" forge){THEN}"
    )


def test_a_unit_left_expired_in_the_tree_is_deleted_before_anything_else(tmp_path: Path) -> None:
    root = unit(tmp_path, **{ONE: RESOLVED})
    step = route.decide(root, None)
    assert (step.command, step.stage, step.then) == (route.EXPIRE, "expire", route.NEXT)
    assert step.why == (
        f"{ONE}, {SPEC} have expired and are still in the tree: this deletes them and carries"
        " their `## Answers` lines to the design brief's `## In the owner's words`"
    )


def test_the_listing_of_an_expired_unit_is_cut_where_it_is_long(tmp_path: Path) -> None:
    more = {f".scratch/f/issues/0{n}-x.md": RESOLVED for n in range(1, 6)}
    step = route.decide(unit(tmp_path, **more), None)
    assert step.stage == "expire"
    assert " ... have expired and are still in the tree" in step.why


def test_after_the_deleter_has_run_the_records_stage_names_the_merge(tmp_path: Path) -> None:
    root = unit(tmp_path, **{ONE: RESOLVED, "guards/manifest.toml": MANIFEST})
    assert documents.main(["documents.py", "expire", "--root", str(root)]) == 0
    step = route.decide(root, None)
    assert step.stage == "records"
    assert step.command == "`just merge --records`"


def test_a_lone_expired_ticket_is_named_in_the_singular(tmp_path: Path) -> None:
    root = repository(tmp_path, {**ADOPTED, "docs/design-brief.md": BRIEF, ONE: RESOLVED})
    step = route.decide(root, None)
    assert step.why == (
        f"{ONE} has expired and is still in the tree: this deletes it and carries its"
        " `## Answers` lines to the design brief's `## In the owner's words`"
    )


def test_the_deleter_runs_under_the_plugin_interpreter_form() -> None:
    assert route.EXPIRE.startswith(PLUGIN_FORM)
    assert route.EXPIRE.endswith('/scripts/documents.py" expire')


def test_a_spec_with_tickets_beside_it_is_never_offered_to_split(tmp_path: Path) -> None:
    files = {
        ".scratch/split/spec.md": "# Split\n",
        ".scratch/split/issues/01-a.md": "# A\n\nStatus: ready-for-agent\n",
    }
    assert route.unsplit_spec(repository(tmp_path, files)) is None
