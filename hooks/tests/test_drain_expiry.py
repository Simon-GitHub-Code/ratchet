"""``drain.py`` and a steering document that leaves with its feature: ADR 0064's replays.

Two tickets, alpha and gamma, share the unit ``.scratch/f/``. Each is built in a work tree of its
own, ``expire`` runs after its content commit, and the one that lands last deletes the unit.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

import documents
import drain
import hooks_installed
import lowered
import merge_ready
import queue_notes
from tests.repos import commit, git, repository, write
from tests.test_drain import (
    ALPHA,
    BETA,
    BRANCH_A,
    BRANCH_C,
    FILES,
    GAMMA,
    STAMP,
    approval,
    build,
    note,
    run,
    ticket,
)

Out = pytest.CaptureFixture[str]
SPEC = ".scratch/f/spec.md"
BRIEF = documents.BRIEF
WORDS = "## In the owner's words"
BRIEF_TEXT = f"# Brief\n\n{WORDS}\n\n<The owner's questions.>\n\n## Purpose\n\nA purpose.\n"
THRESHOLDS = "quality/thresholds.toml"
OWNERS = "- the owner's later line"


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks_installed, "check", lambda _root: None)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(drain, "now", lambda: STAMP)


def answered(text: str, *lines: str) -> str:
    return text + "\n## Answers\n\n" + "".join(f"- {line}\n" for line in lines)


def world(tmp_path: Path) -> Path:
    files = {name: text for name, text in FILES.items() if name != BETA}
    files |= {
        ALPHA: answered(ticket(1, "Alpha"), "alpha's line"),
        GAMMA: answered(ticket(3, "Gamma"), "gamma's line"),
        SPEC: answered("# F\n", "the unit's line"),
        BRIEF: BRIEF_TEXT,
        THRESHOLDS: "[gate]\nfloor = 1\n",
    }
    root = tmp_path / "repo"
    root.mkdir()
    return repository(root, files)


THE_TWO = {
    ALPHA: (BRANCH_A, "alpha", 1, "Alpha"),
    GAMMA: (BRANCH_C, "gamma", 3, "Gamma"),
}


def committed(root: Path, ref: str, *, at: str | None = None) -> Path:
    """Open ``ref``, and commit its content: the ticket resolved and a feature spec of its own."""
    branch, slug, number, title = THE_TWO[ref]
    run(root, "open", ref)
    resolved = answered(ticket(number, title, status="resolved"), f"{slug}'s line")
    spec = answered(f"# {title}\n", f"{slug}'s spec line")
    return build(root, branch, {ref: at or resolved, f"docs/features/{slug}/spec.md": spec})


def expired(tree: Path) -> None:
    assert documents.main(["documents.py", "expire", "--root", str(tree)]) == 0
    commit(tree, "docs: what has expired")


def verdict(tree: Path, ref: str) -> None:
    assert merge_ready.write_record(tree, json.dumps(approval(THE_TWO[ref][1]))) == 0
    commit(tree, "docs: the verdict record")


def built(root: Path, ref: str) -> Path:
    tree = committed(root, ref)
    expired(tree)
    verdict(tree, ref)
    return tree


def rejudged(root: Path, tree: Path, ref: str) -> None:
    assert run(root, "check", ref, "--final") == 0
    verdict(tree, ref)


def lines(root: Path) -> list[str]:
    text = (root / BRIEF).read_text(encoding="utf-8")
    return [row for row in documents.markdown_text.section(text, WORDS).split("\n") if row]


def carried(root: Path) -> list[str]:
    return sorted(documents.parts(row)[1] for row in lines(root) if documents.parts(row)[0])


def merge_in_progress(tree: Path) -> bool:
    return drain.rebasing(tree)


def owner_adds_a_line(root: Path, path: str = ALPHA) -> None:
    text = (root / path).read_text(encoding="utf-8")
    write(root, path, text + f"{OWNERS}\n")
    commit(root, "docs(tracker): the owner's answer")


def landed(root: Path, *refs: str) -> None:
    for ref in refs:
        assert run(root, "land", ref) == 0


UNIT = [
    "alpha's line",
    "alpha's spec line",
    "gamma's line",
    "gamma's spec line",
    "the unit's line",
]


def test_whichever_ticket_lands_last_deletes_the_unit(tmp_path: Path, capsys: Out) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree_a, tree_c = built(root, ALPHA), built(root, GAMMA)
    landed(root, ALPHA)
    assert (root / ".scratch/f").is_dir()
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert not (tree_c / ".scratch/f").exists()
    assert sorted(documents.parts(row)[1] for row in lines(tree_c)[1:]) == UNIT
    rejudged(root, tree_c, GAMMA)
    capsys.readouterr()
    assert run(root, "land", GAMMA) == 0
    assert f"closed: {GAMMA}: merged" in capsys.readouterr().out
    assert not (root / ".scratch/f").exists()
    assert not list(root.glob("docs/features/*/spec.md"))
    assert carried(root) == UNIT
    assert not tree_a.exists()


def test_a_records_edit_between_expire_and_update_reaches_the_brief_once_and_lands(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    built(root, ALPHA)
    landed(root, ALPHA)
    tree = built(root, GAMMA)
    assert not (tree / ".scratch/f").exists()
    owner_adds_a_line(root)
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert not merge_in_progress(tree)
    assert not (tree / ".scratch/f").exists()
    assert sorted(documents.parts(row)[1] for row in lines(tree)[1:]) == sorted(
        [*UNIT, OWNERS.removeprefix("- ")]
    )
    rejudged(root, tree, GAMMA)
    landed(root, GAMMA)
    assert not (root / ".scratch/f").exists()


def test_a_judge_again_run_after_two_updates_reads_the_spec_from_the_commit_that_deleted_it(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree_a = committed(root, ALPHA)
    expired(tree_a)
    built(root, GAMMA)
    landed(root, GAMMA)
    assert run(root, "update", ALPHA) == 0
    owner_adds_a_line(root, GAMMA)
    assert run(root, "update", ALPHA) == 0
    assert not merge_in_progress(tree_a)
    path = "docs/features/alpha/spec.md"
    deleter = git(tree_a, "log", "--diff-filter=D", "-1", "--format=%H", "--", path)
    assert git(tree_a, "show", f"{deleter}~1:{path}").startswith("# Alpha")
    rejudged(root, tree_a, ALPHA)
    capsys.readouterr()
    assert run(root, "land", ALPHA) == 0
    assert f"closed: {ALPHA}: merged" in capsys.readouterr().out
    assert not (root / ".scratch/f").exists()


def test_a_land_killed_before_its_note_and_run_again_records_the_merge(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = world(tmp_path)
    built(root, ALPHA)
    landed(root, ALPHA)
    built(root, GAMMA)

    def die(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(drain, "close", die)
        with pytest.raises(KeyboardInterrupt):
            run(root, "land", GAMMA)
    assert not (root / GAMMA).exists()
    capsys.readouterr()
    assert run(root, "land", GAMMA) == 0
    assert capsys.readouterr().out.endswith(f"closed: {GAMMA}: merged\n")
    assert note(root, BRANCH_C)["outcome"] == "merged"


def test_every_command_that_takes_a_ticket_finds_it_by_its_note_when_its_file_is_gone(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    built(root, ALPHA)
    landed(root, ALPHA)
    built(root, GAMMA)
    landed(root, GAMMA)
    assert not (root / GAMMA).exists()
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 1
    assert capsys.readouterr().out == f"update: {GAMMA} is merged and is not rebased\n"
    assert run(root, "update", "3") == 1
    assert run(root, "update", "9") == 2
    assert "no such ticket" in capsys.readouterr().out


def test_a_note_that_names_no_local_ticket_finds_nothing(tmp_path: Path) -> None:
    root = world(tmp_path)
    queue_notes.write_note(queue_notes.note_path(root, "feature/9-x"), {"ref": "#9", "title": "X"})
    queue_notes.write_note(queue_notes.note_path(root, "feature/8-y"), {"ref": 8})
    assert drain.noted(root, "#9") is None
    assert drain.noted(root, "8") is None


def test_an_update_by_plain_git_leaves_the_unit_and_the_judge_says_so(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    built(root, ALPHA)
    tree = built(root, GAMMA)
    landed(root, ALPHA)
    git(tree, "reset", "--hard", "HEAD~1")
    git(tree, "rebase", "-X", "theirs", "main")
    verdict(tree, GAMMA)
    queue_notes.write_note(
        queue_notes.note_path(root, BRANCH_C), {**note(root, BRANCH_C), "passed": True}
    )
    capsys.readouterr()
    assert run(root, "land", GAMMA) == 1
    out = capsys.readouterr().out
    assert "expired: .scratch/f has expired and the change leaves it in the tree" in out
    assert "`documents.py expire`" in out
    assert (root / ".scratch/f").is_dir()


def test_a_conflict_resolved_to_the_deletion_alone_is_red_for_ratchet_lowered(
    tmp_path: Path,
) -> None:
    root = world(tmp_path)
    built(root, ALPHA)
    landed(root, ALPHA)
    tree = built(root, GAMMA)
    owner_adds_a_line(root)
    git(tree, "reset", "--hard", "HEAD~1")
    stopped = subprocess.run(  # noqa: S603
        ["git", "-C", str(tree), "rebase", "main"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert "CONFLICT (modify/delete)" in stopped.stdout
    git(tree, "rm", "-q", "--", ALPHA)
    git(tree, "-c", "core.editor=true", "rebase", "--continue")
    assert lowered.main(["lowered.py", "--root", str(tree)]) == 1
    documents.expire(tree, STAMP.date())
    assert lowered.main(["lowered.py", "--root", str(tree)]) == 1


def test_a_hand_squash_loses_the_commit_that_deleted_the_spec_and_the_update_goes_on(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    tree = committed(root, ALPHA)
    expired(tree)
    git(tree, "reset", "--soft", "main")
    commit(tree, "feat(core): alpha, by hand")
    owner_adds_a_line(root, GAMMA)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    path = "docs/features/alpha/spec.md"
    assert git(tree, "log", "--diff-filter=D", "--format=%H", "--", path) == ""
    rejudged(root, tree, ALPHA)
    landed(root, ALPHA)


def test_the_final_check_is_green_after_an_update_that_committed_through_expire(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    built(root, ALPHA)
    tree = built(root, GAMMA)
    landed(root, ALPHA)
    before = git(tree, "rev-parse", "HEAD")
    assert run(root, "update", GAMMA) == 0
    assert git(tree, "rev-list", "--count", f"{before}..HEAD") != "0"
    assert "docs: delete what has expired" in git(tree, "log", "-1", "--format=%s")
    capsys.readouterr()
    assert run(root, "check", GAMMA, "--final") == 0
    assert "a role" not in capsys.readouterr().out


def test_an_update_recovered_after_a_kill_past_its_rebase_expires_the_unit_too(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    built(root, ALPHA)
    tree = built(root, GAMMA)
    landed(root, ALPHA)
    with monkeypatch.context() as patch:
        patch.setattr(drain, "after_rebase", lambda _tree: (_ for _ in ()).throw(KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            run(root, "update", GAMMA)
    assert (tree / ".scratch/f").is_dir()
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert not (tree / ".scratch/f").exists()


def test_an_update_whose_brief_has_no_place_for_the_lines_says_so_and_goes_on(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    built(root, ALPHA)
    tree = committed(root, GAMMA)
    landed(root, ALPHA)
    write(root, BRIEF, "# Brief\n")
    commit(root, "docs: a brief with no place")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert "has no `## In the owner's words` section to carry" in capsys.readouterr().err
    assert (tree / ".scratch/f").is_dir()


def brief_with(*rows: str) -> str:
    held = "".join(f"{row}\n" for row in rows)
    return f"# Brief\n\n{WORDS}\n\n<The owner's questions.>\n{held}\n## Purpose\n\nA purpose.\n"


def test_a_conflict_in_the_owners_words_keeps_every_line_either_side_holds(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree = committed(root, GAMMA)
    write(tree, BRIEF, brief_with("- 2026-02-01: the branch's"))
    commit(tree, "docs: the branch's words")
    write(root, BRIEF, brief_with("- 2026-01-01: main's"))
    commit(root, "docs: main's words")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert [documents.parts(row)[1] for row in lines(tree)[:3]] == [
        "<The owner's questions.>",
        "main's",
        "the branch's",
    ]


def test_a_unit_main_deleted_while_the_branch_changed_it_is_taken_and_deleted_again(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree = committed(root, GAMMA)
    write(root, BRIEF, brief_with(*()))
    git(root, "rm", "-q", "-r", "--", ".scratch/f")
    commit(root, "docs: deleted by hand")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert not (tree / ".scratch/f").exists()
    assert "gamma's line" in carried_bodies(tree)


def test_a_modify_delete_conflict_of_a_file_that_is_no_steering_document_goes_to_reconcile(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree = committed(root, GAMMA)
    write(tree, "src/a.py", "A = 2\n")
    commit(tree, "feat: the branch's edit")
    git(root, "rm", "-q", "--", "src/a.py")
    commit(root, "feat: main deletes it")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 2
    assert json.loads(capsys.readouterr().out) == {"moved": False, "conflicts": ["src/a.py"]}


def test_an_update_settles_each_stop_of_a_rebase_that_stops_twice(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree = committed(root, GAMMA)
    write(tree, ALPHA, answered(ticket(1, "Alpha", status="resolved"), "alpha's line"))
    commit(tree, "docs(tracker): alpha resolved")
    git(root, "rm", "-q", "-r", "--", ".scratch/f")
    commit(root, "docs: deleted by hand")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert not (tree / ".scratch/f").exists()


def carried_bodies(tree: Path) -> list[str]:
    return [documents.parts(row)[1] for row in lines(tree)]


@pytest.mark.parametrize(
    "outside",
    [
        {"src/a.py": "A = 2\n"},
        {BRIEF: BRIEF_TEXT.replace("A purpose.", "Main's purpose.")},
    ],
    ids=["code", "the brief outside the owner's words"],
)
def test_a_conflict_that_is_more_than_the_unit_and_the_owners_words_goes_to_reconcile(
    tmp_path: Path, capsys: Out, outside: dict[str, str]
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree = committed(root, GAMMA)
    for path, text in outside.items():
        write(tree, path, text.replace("Main's", "The branch's").replace("A = 2", "A = 3"))
        commit(tree, "feat: the branch's edit")
        write(root, path, text)
        commit(root, "feat: main's edit")
    before = git(tree, "rev-parse", "HEAD")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 2
    said = json.loads(capsys.readouterr().out)
    assert said["moved"] is False
    assert set(said["conflicts"]) <= {"src/a.py", BRIEF}
    assert git(tree, "rev-parse", "HEAD") == before
    assert not merge_in_progress(tree)


def test_a_conflict_in_a_file_both_sides_changed_goes_to_reconcile(
    tmp_path: Path, capsys: Out
) -> None:
    root = world(tmp_path)
    run(root, "open", GAMMA)
    tree = committed(root, GAMMA)
    write(tree, ALPHA, answered(ticket(1, "Alpha"), "the branch's"))
    commit(tree, "docs: the branch's answer")
    write(root, ALPHA, answered(ticket(1, "Alpha"), "main's"))
    commit(root, "docs: main's answer")
    capsys.readouterr()
    assert run(root, "update", GAMMA) == 2
    assert json.loads(capsys.readouterr().out)["conflicts"] == [ALPHA]
