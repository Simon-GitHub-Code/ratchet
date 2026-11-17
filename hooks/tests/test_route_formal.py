"""The route reads the formal model's upkeep and a defect ticket from the binding and branches."""

from pathlib import Path

import pytest

import feature_preflight as preflight
import formal_model
import queue_notes
import route
import triggers
from tests.repos import branch, commit, git, repository, write
from tests.test_route import (
    ADOPTED,
    BINDING,
    CONJECTURE,
    FakeGh,
    hooks_are_installed,
    issues,
    owing,
)

__all__ = ["hooks_are_installed"]

SECTION = (
    "\n## Formal verification\n\n- Model: `formal/**`\n- Mirrors: `src/**/*.py`\n- Synced: {}\n"
)
ROW = "survives failing dependencies"
CALL = "import requests\nrequests.get('http://x')\n"


def adopted(tmp_path: Path) -> tuple[Path, str]:
    root = repository(tmp_path, {**ADOPTED, "src/a.py": "a = 1\n", "formal/m.md": "m\n"})
    return root, git(root, "rev-parse", "HEAD")


def synced(root: Path, commit_id: str, binding: str = BINDING) -> None:
    write(root, "docs/process/binding.md", binding + SECTION.format(commit_id))
    commit(root, "chore(method): bind the formal model")


def kept(root: Path) -> None:
    git(root, "branch", "formal/kept-outbound-call")


def ticket(line: str, name: str = "01-t") -> dict[str, str]:
    body = f"# 01: T\n\nStatus: ready-for-agent\n\n{line}\n" + CONJECTURE
    return {f".scratch/f/issues/{name}.md": body}


def test_the_section_is_three_lines_and_a_binding_without_mirrors_has_none() -> None:
    formal = formal_model.read(BINDING + SECTION.format("abc123"))
    assert formal == formal_model.Formal(("formal/**",), ("src/**/*.py",), "abc123")
    assert formal_model.read(BINDING) is None
    assert formal_model.read(BINDING + "\n## Formal verification\n\n- Model: `m`\n") is None
    two = BINDING + "\n## Formal verification\n\n- Mirrors: `a/**`, `b/*.py`\n"
    assert formal_model.read(two) == formal_model.Formal((), ("a/**", "b/*.py"), "")


def test_a_ticket_line_is_a_defect_of_a_row_or_a_round_and_may_name_the_kept_branch() -> None:
    def read(line: str) -> preflight.FormalModel | None:
        return preflight.local_ticket("t.md", f"# T\n\n{line}\n").formal_model

    kept = "formal/kept-state-machine"
    assert read(f"Formal model: defect `{ROW}`, kept on `{kept}`") == preflight.FormalModel(
        ROW, kept
    )
    assert read(f"**Formal model:** defect `{ROW}`") == preflight.FormalModel(ROW, None)
    assert read("Formal model: round") == preflight.FormalModel(None, None)
    assert read(f"Formal model: round, kept on `{kept}`") == preflight.FormalModel(None, kept)
    assert read("Formal model: round").round  # type: ignore[union-attr]
    assert read("Formal model: something else") is None
    assert read("Status: ready-for-agent") is None


def test_a_mirrored_commit_the_sync_cannot_reach_is_behind_whatever_its_date(
    tmp_path: Path,
) -> None:
    root, base = adopted(tmp_path)
    git(root, "switch", "-q", "-c", "late")
    write(root, "src/a.py", "a = 2\n")
    commit(root, "feat: a mirrored change on a branch")
    git(root, "switch", "-q", "main")
    write(root, "README.md", "# B\n")
    sync = commit(root, "docs: a change the model does not mirror")
    formal = formal_model.Formal(("formal/**",), ("src/**/*.py",), sync)
    assert formal_model.behind(root, "main", formal) is None
    git(root, "merge", "-q", "--no-ff", "late", "-m", "Merge branch late")
    late = formal_model.behind(root, "main", formal)
    assert late is not None
    assert (late.synced, len(late.commit)) == (sync[:12], 12)
    assert formal_model.behind(root, "main", formal_model.Formal((), ("src/**/*.py",), base))


def test_a_move_out_of_a_mirrored_path_is_behind(tmp_path: Path) -> None:
    root, base = adopted(tmp_path)
    git(root, "mv", "src/a.py", "elsewhere.py")
    commit(root, "refactor: move the module out")
    assert formal_model.behind(root, "main", formal_model.Formal((), ("src/**/*.py",), base))


def test_a_clone_without_the_sync_commit_and_a_binding_without_one_read_as_behind(
    tmp_path: Path,
) -> None:
    root, _ = adopted(tmp_path)
    gone = formal_model.Formal((), ("src/**",), "f" * 40)
    assert formal_model.behind(root, "main", gone) == formal_model.Behind("f" * 12)
    assert formal_model.behind(root, "main", formal_model.Formal((), ("src/**",), "")) == (
        formal_model.Behind("")
    )


def test_the_upkeep_branches_waiting_are_those_the_default_branch_does_not_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = adopted(tmp_path)
    assert not formal_model.started(root)
    assert formal_model.waiting(root, "main") is None
    for name in ("formal/kept-state-machine", "formal/kept-upkeep-abc", "formal/upkeep-merged"):
        branch(root, name)
        write(root, f"{name}.md", "x\n")
        commit(root, f"chore: {name}")
        git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "formal/upkeep-merged")
    assert formal_model.started(root)
    assert formal_model.waiting(root, "main") is None
    for name, date in (("formal/upkeep-old", "2020-01-01"), ("formal/upkeep-new", "2021-01-01")):
        monkeypatch.setenv("GIT_COMMITTER_DATE", f"{date}T00:00:00Z")
        branch(root, name)
        write(root, f"{name}.md", "x\n")
        commit(root, f"chore: {name}")
        git(root, "switch", "-q", "main")
    assert formal_model.waiting(root, "main") == "formal/upkeep-old"


def test_a_repository_with_no_model_and_no_formal_branch_has_no_upkeep(tmp_path: Path) -> None:
    root, _ = adopted(tmp_path)
    checkout = route.observe(root)
    assert checkout is not None
    assert route.upkeep(root, None, checkout) == route.Upkeep()
    assert route.formal_step(route.Upkeep(), checkout, route.Step("x", "y", "z", "s")) is None


def test_a_model_in_sync_names_nothing_and_one_behind_names_the_safeguard_beside_the_build(
    tmp_path: Path,
) -> None:
    root, base = adopted(tmp_path)
    synced(root, base)
    assert route.decide(root, None).command == route.SPEC
    write(root, "src/a.py", "a = 2\n")
    mirrored = commit(root, "feat: a mirrored change")
    step = route.decide(root, None)
    assert step == route.Step(
        route.SAFEGUARD,
        f"the formal model is behind the code it mirrors: commit {mirrored[:12]} on `main`"
        f" changed mirrored code after the `- Synced:` commit {base[:12]}, so a round checks the"
        f" model against it. Or carry on with {route.SPEC}: nothing is red for it",
        route.SPEC,
        "formal-upkeep",
    )


def test_a_clone_without_the_sync_commit_is_told_behind_and_why(tmp_path: Path) -> None:
    root, _ = adopted(tmp_path)
    synced(root, "f" * 40)
    step = route.decide(root, None)
    assert step.stage == "formal-upkeep"
    assert step.why == (
        "the formal model may be behind: this clone does not hold the `- Synced:` commit"
        f" {'f' * 12}, so it cannot tell; fetch the default branch's full history, then run"
        f" this again. Or carry on with {route.SPEC}: nothing is red for it"
    )


def test_an_owed_row_comes_before_the_upkeep(tmp_path: Path) -> None:
    root = owing(tmp_path, **{"src/a.py": "a = 1\n"})
    base = git(root, "rev-parse", "HEAD")
    synced(root, base, (root / "docs/process/binding.md").read_text(encoding="utf-8"))
    write(root, "src/b.py", CALL)
    commit(root, "feat: the first outbound call")
    assert route.decide(root, None).stage == "safeguard"


def test_a_round_ticket_is_work_and_wins_over_a_waiting_branch_and_behind(tmp_path: Path) -> None:
    root, base = adopted(tmp_path)
    synced(root, base)
    write(root, "src/a.py", "a = 2\n")
    commit(root, "feat: a mirrored change")
    branch(root, "formal/upkeep-abc")
    write(root, "formal/m.md", "n\n")
    commit(root, "chore: the round's change")
    git(root, "switch", "-q", "main")
    assert route.decide(root, None).stage == "formal-round"
    for path, text in ticket("Formal model: round, kept on `formal/kept-upkeep-abc`").items():
        write(root, path, text)
    commit(root, "docs(tracker): the round's ticket")
    step = route.decide(root, None)
    assert (step.command, step.stage) == (
        f"{route.FEATURE} .scratch/f/issues/01-t.md",
        "formal-ticket",
    )
    assert step.why == (
        'ticket .scratch/f/issues/01-t.md "T" carries what the formal model\'s last round found,'
        " and fixing it is the model's next step. Or carry on with"
        f" {route.FEATURE} .scratch/f/issues/01-t.md: nothing is red for it"
    )


def test_a_round_waiting_names_the_owners_merge_and_a_kept_branch_never_waits(
    tmp_path: Path,
) -> None:
    root, base = adopted(tmp_path)
    synced(root, base)
    branch(root, "formal/kept-upkeep-abc")
    write(root, "formal/m.md", "n\n")
    commit(root, "chore: an update that found a bug")
    git(root, "switch", "-q", "main")
    assert route.decide(root, None).command == route.SPEC
    branch(root, "formal/upkeep-abc")
    write(root, "formal/m.md", "o\n")
    commit(root, "chore: the round's change")
    git(root, "switch", "-q", "main")
    step = route.decide(root, None)
    assert (
        step.command
        == "`git switch main && git merge --no-ff formal/upkeep-abc`, typed by the owner"
    )
    assert step.why == (
        "branch `formal/upkeep-abc` holds a round of the formal model's upkeep, and it changes"
        " guard paths, so it waits for your merge. Or carry on with"
        f" {route.SPEC}: nothing is red for it"
    )


def test_a_binding_with_no_sync_commit_says_it_was_never_checked(tmp_path: Path) -> None:
    root, _ = adopted(tmp_path)
    synced(root, "")
    assert route.decide(root, None).why == (
        "the formal model has never been checked against the code: the binding's `- Synced:`"
        f" line names no commit. Or carry on with {route.SPEC}: nothing is red for it"
    )


def test_a_model_kept_at_binding_with_no_section_has_no_state_to_name(tmp_path: Path) -> None:
    root, _ = adopted(tmp_path)
    branch(root, "formal/kept-state-machine")
    write(root, "formal/m.md", "n\n")
    commit(root, "chore: the kept model")
    git(root, "switch", "-q", "main")
    assert route.decide(root, None).command == route.SPEC


def test_a_ticket_on_the_tracker_is_read_as_a_local_one_is(tmp_path: Path) -> None:
    root, base = adopted(tmp_path)
    synced(root, base)
    body = f"Formal model: round\n{CONJECTURE}"
    gh = FakeGh(issues=issues((7, "Round", body)))
    step = route.decide(root, gh)
    assert (step.command, step.stage) == (f"{route.FEATURE} 7", "formal-ticket")
    assert [t.ref for t in route.formal_tickets(root, gh)] == ["7"]


def test_an_open_defect_ticket_takes_its_row_from_the_safeguard_and_the_others_stay(
    tmp_path: Path,
) -> None:
    files = ticket(f"Formal model: defect `{ROW}`, kept on `formal/kept-outbound-call`")
    root = owing(tmp_path, **files)
    kept(root)
    write(root, "src/a.py", CALL)
    commit(root, "feat: the first outbound call")
    assert [row.question for row in triggers.owed(root)] == [ROW]
    step = route.decide(root, None)
    assert (step.command, step.stage) == (
        f"{route.CONJECTURE} .scratch/f/issues/01-t.md",
        "conjecture",
    )
    other = ticket("Formal model: defect `some other row`")
    write(root, next(iter(other)), next(iter(other.values())))
    commit(root, "docs(tracker): the defect names another row")
    assert route.decide(root, None).stage == "safeguard"


def test_an_unlabelled_local_defect_ticket_also_takes_its_row(tmp_path: Path) -> None:
    text = f"# 01: T\n\nStatus: needs-triage\n\nFormal model: defect `{ROW}`\n"
    root = owing(tmp_path, **{".scratch/f/issues/01-t.md": text})
    kept(root)
    write(root, "src/a.py", CALL)
    commit(root, "feat: the first outbound call")
    assert route.decide(root, None).stage != "safeguard"


def hold(owes: list[str]) -> queue_notes.Hold:
    return queue_notes.Hold(["red"], [], owes, [])


@pytest.mark.parametrize(
    ("line", "owes", "expected"),
    [
        (
            f"Formal model: defect `{ROW}`",
            ["outbound-call"],
            ["change ticket 7 to meet what the drain parked it for"],
        ),
        (
            f"Formal model: defect `{ROW}`",
            ["outbound-call", "listener"],
            [
                "run /ratchet:safeguard for listener",
                "change ticket 7 to meet what the drain parked it for",
            ],
        ),
        (f"Formal model: defect `{ROW}`", ["listener"], ["run /ratchet:safeguard for listener"]),
        ("Formal model: round", ["outbound-call"], ["run /ratchet:safeguard for outbound-call"]),
        ("Formal model: defect `no such row`", ["x"], ["run /ratchet:safeguard for x"]),
        ("", ["outbound-call"], ["run /ratchet:safeguard for outbound-call"]),
    ],
    ids=["its own kind", "and another", "another kind", "a round", "an unknown row", "no line"],
)
def test_a_defect_ticket_parked_for_its_own_kind_is_told_to_change_not_to_run_the_safeguard(
    line: str, owes: list[str], expected: list[str]
) -> None:
    parked = preflight.local_ticket("7", f"# T\n\n{line}\n")
    assert route.releases(hold(owes), parked, "main") == expected


def test_a_round_ticket_with_no_conjecture_gets_the_ordinary_conjecture_step_first(
    tmp_path: Path,
) -> None:
    root, base = adopted(tmp_path)
    synced(root, base)
    text = "# 01: T\n\nStatus: ready-for-agent\n\nFormal model: round\n"
    write(root, ".scratch/f/issues/01-t.md", text)
    commit(root, "docs(tracker): a round's ticket with no conjecture")
    step = route.decide(root, None)
    assert (step.command, step.stage) == (
        f"{route.CONJECTURE} .scratch/f/issues/01-t.md",
        "conjecture",
    )
    write(root, ".scratch/f/issues/01-t.md", text + CONJECTURE)
    commit(root, "docs(tracker): the conjecture is stated")
    assert route.decide(root, None).stage == "formal-ticket"
