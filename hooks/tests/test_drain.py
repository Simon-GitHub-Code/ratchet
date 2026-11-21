import atexit
import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import drain
import hooks_installed
import merge_ready
import owed
import queue_notes
import triggers
from tests import servers
from tests.repos import commit, git, hook_script, repository, write
from tests.test_merge_ready import GLOSSARY, MANIFEST

Out = pytest.CaptureFixture[str]
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CONJECTURE = (
    "\n\n## Conjecture\n\nOne function per ticket; the refuter is a failing call with two"
    " operands. Alternatives: a class (killed: nothing holds state).\n"
)
ALPHA, BETA, GAMMA = (f".scratch/f/issues/0{n}-{name}.md" for n, name in enumerate("abc", 1))
BRANCH_A, BRANCH_B, BRANCH_C = "feature/1-alpha", "feature/2-beta", "feature/3-gamma"
STAMP = datetime(2026, 11, 12, 9, 30, 5, tzinfo=UTC)
UNSTAMPED = "run time: not measured, since the run has no start stamp"
REAL_NOW = drain.now
ASSUMED = {"question": "Which case?", "assumed": "Lower.", "refuter": "A user types upper."}


def ticket(number: int, title: str, extra: str = "", *, status: str = "ready-for-agent") -> str:
    return f"# 0{number}: {title}\n\n{extra}Status: {status}{CONJECTURE}"


def binding(merge: str | None, check: str = "true") -> str:
    rows = [("check", check), ("merge", merge)]
    table = "".join(f"| {name} | `{command}` |\n" for name, command in rows if command)
    pin = f"- Ratchet plugin: 999.0.0 at {'0' * 40}\n"
    head = (
        f"# Binding\n\n{pin}- Default branch: `main`\n\n| Entry point | Command |\n| --- | --- |\n"
    )
    return head + table


MERGE = f"{sys.executable} {SCRIPTS / 'merge.py'}"
FILES = {
    "guards/manifest.toml": MANIFEST,
    "GLOSSARY.md": GLOSSARY,
    "docs/process/binding.md": binding(MERGE),
    "src/a.py": "A = 1\n",
    ALPHA: ticket(1, "Alpha"),
    BETA: ticket(2, "Beta", "Blocked by: 01\n\n"),
    GAMMA: ticket(3, "Gamma"),
}


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks_installed, "check", lambda _root: None)
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(drain, "now", lambda: STAMP)


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return repository(root, {**FILES, **files})


def offline(root: Path, concurrency: int = 3) -> drain.Drain:
    return drain.Drain(root, None, concurrency)


def states(root: Path, concurrency: int = 3) -> dict[str, str]:
    return {entry.branch: entry.state for entry in drain.plan(offline(root, concurrency)).tickets}


def run(root: Path, *argv: str) -> int:
    return drain.main(["drain", *argv, "--root", str(root), "--offline"])


def build(
    root: Path, branch: str, files: dict[str, str], *, record: dict[str, object] | None = None
) -> Path:
    tree = drain.tree_of(root, branch)
    checked = note(root, branch)
    if "start" in checked:
        queue_notes.write_note(queue_notes.note_path(root, branch), {**checked, "passed": True})
    for path, text in files.items():
        write(tree, path, text)
    commit(tree, "feat(core): the change")
    if record is not None:
        assert merge_ready.write_record(tree, json.dumps(record)) == 0
        commit(tree, "docs(core): the verdict record")
    return tree


def approval(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "ticket": "1",
        "verdict": "approve",
        "unresolved": [],
        "openQuestions": [],
        "assumptions": [ASSUMED],
    }


LIFE_DEPENDED_ON = "\n- Life events: depended-on\n"


def note(root: Path, branch: str) -> dict[str, object]:
    return queue_notes.read_note(root, branch)


def kept_said(root: Path, branch: str) -> str:
    [path] = drain.kept_home(root, branch).glob("*.kept")
    return (
        f"kept: what the work tree of `{branch}` held uncommitted is set aside at {path}:"
        f" `drain.py restore {branch}` puts it back in the checkout"
    )


def restored_text(root: Path, branch: str, path: str) -> str:
    assert run(root, "restore", branch) == 0
    return (root / path).read_text(encoding="utf-8")


def test_the_plan_orders_tickets_by_their_blockers_and_starts_the_buildable(tmp_path: Path) -> None:
    root = project(tmp_path)
    drafted = drain.plan(offline(root))
    assert [(e.ref, e.state, e.waits_on) for e in drafted.tickets] == [
        (ALPHA, "buildable", ()),
        (GAMMA, "buildable", ()),
        (BETA, "waiting", (ALPHA,)),
    ]
    assert drafted.start == (ALPHA, GAMMA)
    assert (drafted.base, drafted.forge, drafted.blocked) == ("main", False, ())
    assert drafted.agents_at_most == 3 * (drain.YOUNG_PIPELINE_AGENTS + drain.UPDATE_AGENTS) - 2
    home = Path(tempfile.gettempdir()).resolve() / f"ratchet-queue-{os.getuid()}"
    assert Path(drafted.tickets[0].worktree).parent.parent == home
    assert Path(drafted.tickets[0].worktree).name == "feature-1-alpha"
    assert root not in Path(drafted.tickets[0].worktree).parents


def test_a_depended_on_project_counts_the_critic_and_its_fix_loop(tmp_path: Path) -> None:
    root = project(tmp_path, **{"docs/process/binding.md": binding(MERGE) + LIFE_DEPENDED_ON})
    assert (
        drain.plan(offline(root)).agents_at_most
        == 3 * (drain.PIPELINE_AGENTS + drain.UPDATE_AGENTS) - 2
    )


DECLARED_AGENTS = json.loads(
    re.findall(
        r"^const AGENTS_AT_MOST = (\{.*\})$",
        (SCRIPTS.parent / "workflows/pipeline.js").read_text(encoding="utf-8"),
        re.MULTILINE,
    )[0]
)


@pytest.mark.parametrize(
    ("life", "role"),
    [("", "young"), (LIFE_DEPENDED_ON, "dependedOn")],
    ids=["young", "depended-on"],
)
def test_the_agents_a_plan_counts_are_the_ones_the_workflow_declares(
    tmp_path: Path, life: str, role: str
) -> None:
    root = project(tmp_path, **{"docs/process/binding.md": binding(MERGE) + life})
    update = DECLARED_AGENTS["rejudge"]
    assert drain.plan(offline(root)).agents_at_most == 3 * (DECLARED_AGENTS[role] + update) - update


def test_main_prints_the_plan_as_json(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "plan", "--concurrency", "1") == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["start"] == [ALPHA]
    assert printed["concurrency"] == 1
    assert printed["tickets"][2]["waits_on"] == [ALPHA]


def test_a_checkout_that_is_not_fit_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    git(root, "switch", "-q", "-c", "feature/9-other")
    assert drain.plan(offline(root)).blocked == (
        "branch: HEAD is `feature/9-other`, not `main` -> `git switch main`",
    )
    git(root, "switch", "-q", "main")
    write(root, "src/b.py", "draft\n")
    dirty = drain.plan(offline(root))
    assert dirty.start == ()
    assert dirty.blocked[0].startswith("clean: the working tree has changes")
    (root / "src/b.py").unlink()
    problem = hooks_installed.Problem("no hook runs tier 0", "pre-commit install")
    monkeypatch.setattr(hooks_installed, "check", lambda _root: problem)
    unfit = drain.plan(offline(root))
    assert unfit.blocked == ("hooks: no hook runs tier 0 -> the owner: pre-commit install",)
    assert unfit.start == ()
    assert [(entry.state, entry.why) for entry in unfit.tickets] == [
        ("buildable", ("not started, the checkout is not fit: " + unfit.blocked[0],)),
        ("buildable", ("not started, the checkout is not fit: " + unfit.blocked[0],)),
        ("waiting", ()),
    ]
    monkeypatch.setattr(hooks_installed, "check", lambda _root: None)
    ledger = [owed.Entry("docs/process/binding.md", "pins", "has no section", "0.12.0")]
    write(root, "quality/owed.toml", owed.render(ledger))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: the upgrade")
    filling = drain.plan(offline(root))
    assert filling.start == ()
    assert filling.blocked[0].startswith("fill: 1 owed items an agent writes")
    assert filling.blocked[0].endswith("-> /ratchet:fill")


def test_the_preflight_refuses_a_ticket_and_says_what_produces_the_piece(tmp_path: Path) -> None:
    files = {
        ALPHA: "# 01: Alpha\n\nStatus: ready-for-agent\n",
        BETA: ticket(2, "Beta", "Blocked by: 09\n\n"),
        GAMMA: ticket(3, "Gamma", "Blocked by: 04\n\n"),
        ".scratch/f/issues/04-delta.md": ticket(4, "Delta", status="ready-for-human"),
        ".scratch/f/issues/05-eps.md": ticket(5, "Eps", "Blocked by: 06\n\n"),
        ".scratch/f/issues/06-zeta.md": ticket(6, "Zeta", status="resolved"),
    }
    drafted = drain.plan(offline(project(tmp_path, **files)))
    by = {entry.ref: entry for entry in drafted.tickets}
    assert by[ALPHA].state == "refused"
    assert by[ALPHA].why[0].startswith(f"conjecture: {ALPHA} has no `## Conjecture` section")
    assert "/ratchet:conjecture" in by[ALPHA].why[0]
    assert by[BETA].why == ("blocked: blocker 09 was not found",)
    assert by[GAMMA].why == (
        "blocked: blocker .scratch/f/issues/04-delta.md is still open and is not agent-ready",
    )
    assert by[".scratch/f/issues/05-eps.md"].state == "buildable", "a resolved blocker is done"
    assert drafted.start == (".scratch/f/issues/05-eps.md",)


def test_a_ticket_waiting_on_one_the_drain_cannot_merge_is_refused(tmp_path: Path) -> None:
    files = {
        ALPHA: ticket(1, "Alpha", "Blocked by: 02\n\n"),
        BETA: ticket(2, "Beta", "Blocked by: 01\n\n"),
        GAMMA: ticket(3, "Gamma", "Blocked by: 04\n\n"),
        ".scratch/f/issues/04-delta.md": ticket(4, "Delta", "Blocked by: 05\n\n"),
        ".scratch/f/issues/05-eps.md": ticket(5, "Eps"),
    }
    drafted = drain.plan(offline(project(tmp_path, **files)))
    by = {entry.ref: entry for entry in drafted.tickets}
    assert by[ALPHA].state == by[BETA].state == "refused", "a cycle never becomes buildable"
    assert by[ALPHA].why == (f"blocked: waits on {BETA}, which this drain cannot merge (waiting)",)
    assert by[ALPHA].waits_on == ()
    chain = [entry.ref for entry in drafted.tickets if entry.state != "refused"]
    assert chain == [".scratch/f/issues/05-eps.md", ".scratch/f/issues/04-delta.md", GAMMA]
    assert by[GAMMA].state == "waiting"


class FakeGh:
    def __init__(self, **issues: str) -> None:
        self.issues = issues

    def __call__(self, args: Sequence[str]) -> str | None:
        if args[1] == "list":
            return json.dumps(
                [{"number": int(n), "title": f"T{n}", "body": b} for n, b in self.issues.items()]
            )
        body = self.issues.get(args[2])
        return (
            None
            if body is None
            else json.dumps(
                {
                    "number": int(args[2]),
                    "title": f"T{args[2]}",
                    "body": body,
                    "state": "OPEN",
                    "labels": [{"name": "ready-for-agent"}],
                }
            )
        )


def test_tracker_tickets_wait_on_each_other_by_number(tmp_path: Path) -> None:
    root = project(tmp_path)
    git(root, "remote", "add", "origin", "https://example.invalid/r.git")
    gh = FakeGh(**{"3": "Body." + CONJECTURE, "4": "## Blocked by\n\n- #3\n" + CONJECTURE})
    drafted = drain.plan(drain.Drain(root, gh))
    assert drafted.forge is True
    assert [(e.ref, e.state, e.waits_on) for e in drafted.tickets] == [
        ("3", "buildable", ()),
        ("4", "waiting", ("3",)),
    ]


def test_open_makes_the_work_tree_outside_the_repository_and_the_plan_sees_it(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    opened = json.loads(capsys.readouterr().out)
    tree = Path(opened["worktree"])
    assert (opened["branch"], opened["state"], opened["base"]) == (BRANCH_A, "building", "main")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH_A
    assert git(tree, "rev-parse", "HEAD") == git(root, "rev-parse", "main")
    assert git(root, "status", "--porcelain") == "", "the note dirties no work tree"
    assert note(root, BRANCH_A)["opened"] == STAMP.isoformat(timespec="seconds")
    assert states(root)[BRANCH_A] == "building"
    assert drain.plan(offline(root, 1)).start == (), "one is building, and the cap is one"
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    assert states(root)[BRANCH_A] == "built"
    assert drain.plan(offline(root, 1)).start == (), "built, but still settled in its tree"
    assert run(root, "close", ALPHA, "--outcome", "waits") == 0
    assert drain.plan(offline(root, 1)).start == (GAMMA,)


def test_open_is_the_gate_on_the_order_and_the_cap(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "open", BETA) == 1
    assert capsys.readouterr().out == (
        f"open: {BETA} is not started: it is waiting; waits on {ALPHA}\n"
    )
    assert run(root, "open", "nope.md") == 1
    assert "it is not an open agent-ready ticket" in capsys.readouterr().out
    assert run(root, "open") == 1
    assert run(root, "open", ALPHA, "--concurrency", "1") == 0
    capsys.readouterr()
    assert run(root, "open", GAMMA, "--concurrency", "1") == 1
    assert capsys.readouterr().out.endswith("1 tickets are building: the cap\n")
    write(root, "src/b.py", "draft\n")
    assert run(root, "open", GAMMA) == 1
    assert "clean: the working tree has changes" in capsys.readouterr().out
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 2


def test_open_says_so_when_the_branch_is_checked_out_elsewhere(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    git(root, "worktree", "add", "-q", "-b", BRANCH_A, str(tmp_path / "elsewhere"))
    assert run(root, "open", ALPHA) == 1
    assert capsys.readouterr().out.startswith(f"open: no work tree of `{BRANCH_A}` could be made")
    assert not drain.queue_home(root).exists(), "nothing is left behind"


def moved(root: Path, path: str = "src/m.py", text: str = "M = 1\n") -> None:
    write(root, path, text)
    commit(root, "feat(core): merged meanwhile")


def test_update_rebases_a_branch_the_default_branch_moved_under(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "update", ALPHA) == 1
    assert "has no queue work tree" in capsys.readouterr().out
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/b.py", "B = 1\n")
    assert run(root, "update", ALPHA) == 1
    assert "uncommitted changes" in capsys.readouterr().out
    assert run(root, "check", ALPHA, "--final") == 0
    commit(tree, "feat(core): the change")
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": False, "conflicts": []}
    moved(root)
    assert run(root, "update", ALPHA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert (tree / "src/m.py").is_file()
    assert git(tree, "merge-base", "main", "HEAD") == git(root, "rev-parse", "main")


def test_update_leaves_a_conflicting_branch_as_it_was_and_names_the_paths(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/a.py": "A = 2\n"})
    before = git(tree, "rev-parse", "HEAD")
    moved(root, "src/a.py", "A = 3\n")
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 2
    assert json.loads(capsys.readouterr().out) == {"moved": False, "conflicts": ["src/a.py"]}
    assert git(tree, "rev-parse", "HEAD") == before
    assert git(tree, "status", "--porcelain") == ""
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH_A, "no rebase is in progress"


def test_update_brings_a_judged_branch_up_to_date_and_drops_the_record_it_outdated(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"}, record=approval("alpha"))
    moved(root)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert git(tree, "merge-base", "main", "HEAD") == git(root, "rev-parse", "main")
    assert not (tree / "docs/features/alpha/verdict.json").exists(), "the review is of another tree"
    assert (tree / "src/b.py").is_file()


def test_update_refuses_to_move_a_pushed_branch(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    moved(root)
    git(root, "update-ref", "refs/remotes/origin/feature/1-alpha", BRANCH_A)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 1
    assert "the branch is pushed" in capsys.readouterr().out


def test_update_makes_a_work_tree_for_a_built_branch_an_earlier_drain_left(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"}, record=approval("alpha"))
    git(root, "worktree", "remove", "--force", str(drain.tree_of(root, BRANCH_A)))
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": False, "conflicts": []}
    moved(root)
    assert run(root, "update", ALPHA) == 0
    assert json.loads(capsys.readouterr().out) == {"moved": True, "conflicts": []}
    assert git(root, "merge-base", "main", BRANCH_A) == git(root, "rev-parse", "main")
    assert drain.tree_of(root, BRANCH_A) in drain.worktrees(root)


def killed(monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    def stop(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    writing = queue_notes.write_note
    replacing = Path.replace
    calls: list[Path] = []

    def after_write(path: Path, note: dict[str, object]) -> None:
        writing(path, note)
        if "start" in note and "rebasing" not in note:
            stop()

    def torn(self: Path, target: Path) -> Path:
        calls.append(self)
        if len(calls) == 2:
            stop()
        return replacing(self, target)

    if point == "before_rebase":
        monkeypatch.setattr(drain, "rebase_tree", stop)
    elif point == "after_rebase":
        monkeypatch.setattr(drain, "drop_record", stop)
    elif point == "mid_note_write":
        monkeypatch.setattr(Path, "replace", torn)
    else:
        monkeypatch.setattr(queue_notes, "write_note", after_write)


@pytest.mark.parametrize(
    "point", ["before_rebase", "after_rebase", "mid_note_write", "after_note_write"]
)
def test_a_check_after_an_update_killed_at_any_point_never_names_a_role(
    tmp_path: Path, capsys: Out, point: str
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    moved(root)
    capsys.readouterr()
    with pytest.MonkeyPatch.context() as patch:
        killed(patch, point)
        with pytest.raises(KeyboardInterrupt):
            run(root, "update", ALPHA)
    capsys.readouterr()
    code = run(root, "check", ALPHA)
    said = capsys.readouterr().out
    assert "a role published" not in said
    assert note(root, BRANCH_A).get("outcome") != drain.PARKED
    if point in {"after_rebase", "mid_note_write"}:
        assert code == drain.UNREAD
        assert "run `update` again" in said
    else:
        assert code == 0
    assert run(root, "update", ALPHA) == 0
    capsys.readouterr()
    assert run(root, "check", ALPHA, "--final") == 0
    assert "rebasing" not in note(root, BRANCH_A)


def test_a_role_s_commit_after_a_completed_update_still_parks_the_ticket(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    moved(root)
    assert run(root, "update", ALPHA) == 0
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/b.py", "B = 1\n")
    commit(tree, "feat(core): a role's change")
    capsys.readouterr()
    assert run(root, "check", ALPHA) == 1
    assert "a role published" in str(note(root, BRANCH_A)["reasons"])


def test_a_built_branch_behind_the_default_branch_does_not_land_on_its_old_review(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    run(root, "open", GAMMA)
    done = {ALPHA: ticket(1, "Alpha", status="resolved")}
    build(
        root,
        BRANCH_A,
        {"src/b.py": "from a import A\nB = A + 1\n", **done},
        record=approval("alpha"),
    )
    git(root, "worktree", "remove", "--force", str(drain.tree_of(root, BRANCH_A)))
    build(
        root,
        BRANCH_C,
        {"src/a.py": "A = 'now a string'\n", GAMMA: ticket(3, "Gamma", status="resolved")},
        record=approval("gamma"),
    )
    assert run(root, "land", GAMMA) == 0
    base = git(root, "rev-parse", "main")
    entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
    assert (entry.state, entry.why) == ("built", (drain.BEHIND,))
    capsys.readouterr()
    assert run(root, "land", ALPHA) == 1
    assert "behind: the branch does not hold the tip of `main`" in capsys.readouterr().out
    assert git(root, "rev-parse", "main") == base, "nothing landed"


def test_land_merges_what_the_judge_passes_and_removes_the_work_tree(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    change = {
        "src/b.py": "B = 1\n",
        "GLOSSARY.md": GLOSSARY + "\n**Order**:\nWhat a customer buys.\n_Avoid_: basket\n",
        ALPHA: ticket(1, "Alpha", status="resolved"),
    }
    tree = build(root, BRANCH_A, change, record=approval("alpha"))
    write(tree, ".ratchet/timing.jsonl", '{"key": "test", "seconds": 4.25}\nnot json\n{"key": 1}\n')
    write(tree, ".ratchet/run-time.json", '{"start": "2026-11-17T10:00:00Z"}')
    capsys.readouterr()
    assert run(root, "land", ALPHA) == 0
    out = capsys.readouterr().out
    assert "merge: agent\nmerged: `feature/1-alpha` into `main`" in out
    assert out.endswith(f"closed: {ALPHA}: merged\n")
    assert (root / "src/b.py").is_file(), "the default branch moved, and its work tree with it"
    assert not tree.exists()
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not drain.queue_home(root).exists()
    kept = note(root, BRANCH_A)
    assert (kept["outcome"], kept["hold"], kept["reasons"]) == ("merged", False, [])
    assert kept["assumptions"] == [ASSUMED]
    assert kept["additions"] == ["GLOSSARY.md: term Order (avoid: basket)"]
    assert kept["gates"] == {"test": 4.25}
    assert str(kept["run_time"]).startswith("run time: ")
    assert "not measured" not in str(kept["run_time"])
    assert states(root) == {BRANCH_B: "buildable", BRANCH_C: "buildable"}, (
        "the merge resolved the ticket, so what waited on it is buildable"
    )


def test_land_leaves_the_branch_to_the_owner_with_the_judges_reasons(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    capsys.readouterr()
    assert run(root, "land", ALPHA) == 1
    kept = note(root, BRANCH_A)
    assert kept["outcome"] == "waits"
    assert kept["reasons"] == [
        (
            "record: no verdict record (docs/features/<slug>/verdict.json) is part of the change,"
            " so it is not the product of the pipeline"
        )
    ]
    assert not tree.exists()
    assert git(root, "rev-parse", "--verify", BRANCH_A), "the branch stays for the owner"
    assert states(root)[BRANCH_A] == "held", (
        "a ticket left to the owner is held until its text changes, not landed again"
    )


def test_land_needs_the_merge_entry_point_and_no_forge(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, **{"docs/process/binding.md": binding(None)})
    assert run(root, "land", ALPHA) == 2
    assert capsys.readouterr().out.startswith("land: with a forge")
    write(root, "docs/process/binding.md", binding(MERGE))
    commit(root, "docs(process): the merge")
    run(root, "open", ALPHA)
    assert run(root, "check", ALPHA, "--final") == 0
    write(root, "docs/process/binding.md", binding(None))
    commit(root, "docs(process): no merge")
    write(root, "docs/process/binding.md", binding("/no/such/command"))
    commit(root, "docs(process): a merge that cannot run")
    assert run(root, "land", ALPHA) == 2
    assert "could not run" in capsys.readouterr().out
    write(root, "docs/process/binding.md", binding(f"{sys.executable} -c 'raise SystemExit(5)'"))
    commit(root, "docs(process): a merge that fails")
    assert run(root, "land", ALPHA) == 2
    assert "outcome" not in note(root, BRANCH_A), (
        "nothing is closed on an answer that is no verdict"
    )
    write(root, "docs/process/binding.md", binding(MERGE))
    commit(root, "docs(process): the merge")
    git(root, "remote", "add", "origin", "https://example.invalid/r.git")
    assert run(root, "land", ALPHA) == 2


RESULT = {
    "verdict": "blocked",
    "preflight": {"failures": [{"check": "label", "detail": "none", "produce": "the owner"}]},
    "openQuestions": [
        {
            "question": "Does an expired link say so or go home?",
            "kind": "direction",
            "options": ["A page that says it expired", "The home page"],
            "recommendation": "The brief says a reader is never left guessing.",
        },
        "An older run's question",
    ],
    "guardEdits": [{"path": "lint.toml", "edit": "allow the new rule"}],
    "verdictForcedBy": ["1 blocker/major review issue(s)"],
    "verdictRecord": {"unresolved": ["loses the order"]},
    "disputes": [{"path": "docs/disputes/0001-x.md", "recommendation": "raise the limit"}],
}


def test_close_parks_a_ticket_with_the_results_reasons_and_sets_its_draft_aside(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    write(tree, "docs/features/gamma/spec.md", "draft\n")
    result = tmp_path / "result.json"
    result.write_text(json.dumps(RESULT), encoding="utf-8")
    capsys.readouterr()
    code = run(
        root, "close", GAMMA, "--outcome", "parked", "--reason", "first", "--result", str(result)
    )
    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        kept_said(root, BRANCH_C),
        f"closed: {GAMMA}: parked",
    ]
    assert note(root, BRANCH_C)["reasons"] == [
        "first",
        "label: none -> the owner",
        (
            "open question (direction): Does an expired link say so or go home? Options, the"
            " recommended first: A page that says it expired; The home page. Why the first: The"
            " brief says a reader is never left guessing."
        ),
        "open question: An older run's question",
        "guard edit: lint.toml: allow the new rule",
        "1 blocker/major review issue(s)",
        "unresolved: loses the order",
        "dispute: docs/disputes/0001-x.md: raise the limit",
    ]
    assert not tree.exists()
    assert git(root, "stash", "list") == ""
    assert states(root)[BRANCH_C] == "held"
    held = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_C)
    assert held.why[0] == "first"
    assert run(root, "open", GAMMA) == 1
    assert "it is held; first" in capsys.readouterr().out
    write(root, GAMMA, ticket(3, "Gamma") + "\n## Answers\n\nA page that says it expired.\n")
    commit(root, "docs(tracker): the owner's answer")
    assert states(root)[BRANCH_C] == "held", "the answer leaves the guard edits and the dispute"
    write(root, "lint.toml", "allow the new rule\n")
    commit(root, "chore(method): the guard edit the run named")
    assert states(root)[BRANCH_C] == "buildable", "each part of the hold is met"


@pytest.mark.parametrize("text", ["", "Traceback: no result\n", "[1, 2]\n"])
def test_close_refuses_a_result_that_holds_no_json_object_and_writes_no_note(
    tmp_path: Path, capsys: Out, text: str
) -> None:
    root = project(tmp_path)
    result = tmp_path / "result.json"
    result.write_text(text, encoding="utf-8")
    capsys.readouterr()
    code = run(root, "close", GAMMA, "--outcome", "parked", "--result", str(result))
    assert code == 2
    assert capsys.readouterr().out == (
        f"drain: {result} holds no JSON object, so nothing was closed: write the pipeline's"
        " `result` object to it as JSON, then run this again\n"
    )
    assert not queue_notes.note_path(root, BRANCH_C).exists()


PARKED = {
    "verdict": "request_changes",
    "stop": "carried",
    "unresolvedBlockingFindings": [
        {
            "severity": "major",
            "title": "loses the order",
            "breaks": {"record": "ticket", "quote": "The list keeps the saved order."},
        },
        {"severity": "minor", "title": "a nit"},
    ],
    "reviewIssues": [],
    "verdictRecord": {"unresolved": ["loses the order", "a title no list holds"]},
}


def test_a_run_parked_with_no_review_reads_its_findings_with_their_quotes() -> None:
    assert drain.reasons_of(PARKED) == [
        'unresolved: loses the order (breaks ticket: "The list keeps the saved order.")',
        "unresolved: a title no list holds",
    ]


def test_a_refused_reviews_issues_are_read_with_their_quotes_too() -> None:
    issue = {
        "severity": "blocker",
        "title": "drops a promise",
        "breaks": {"record": "spec.md", "quote": "It keeps the order."},
    }
    refused = {"reviewIssues": [issue, "an older run's text"], "verdictRecord": {}}
    assert drain.reasons_of(refused) == [
        'unresolved: drops a promise (breaks spec.md: "It keeps the order.")'
    ]


def test_a_number_names_the_one_local_ticket_it_starts(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, **{".scratch/f/issues/30-other.md": ticket(3, "Other")})
    for spelling in ("01", "#1", "1"):
        assert run(root, "close", spelling, "--outcome", "parked") == 0
    assert note(root, BRANCH_A)["outcome"] == "parked"
    capsys.readouterr()


def test_a_number_that_starts_two_local_tickets_is_refused_with_both_named(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **{".scratch/g/issues/01-twin.md": ticket(1, "Twin")})
    assert run(root, "close", "1") == 2
    out = capsys.readouterr().out
    assert ALPHA in out
    assert ".scratch/g/issues/01-twin.md" in out
    assert run(root, "close", "9") == 2
    assert "no such ticket" in capsys.readouterr().out


def test_close_without_a_result_and_an_unknown_ticket(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "close", "nope.md") == 2
    assert "no such ticket" in capsys.readouterr().out
    assert run(root, "close", ALPHA, "--result", str(tmp_path / "missing.json")) == 2
    assert not queue_notes.note_path(root, BRANCH_A).exists()
    assert run(root, "close", ALPHA) == 0
    kept = note(root, BRANCH_A)
    assert (kept["outcome"], kept["reasons"], kept["assumptions"]) == ("waits", [], [])
    assert (kept["additions"], kept["gates"]) == ([], {}), "a branch that was never made holds none"


def test_clean_removes_merged_and_gone_work_trees_and_keeps_work_in_progress(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    for ref in (ALPHA, GAMMA):
        run(root, "open", ref)
    tree_a = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    tree_c = drain.tree_of(root, BRANCH_C)
    git(root, "merge", "-q", "--no-ff", BRANCH_A, "-m", f"Merge branch '{BRANCH_A}'")
    stray = drain.queue_home(root) / "left-by-a-crash"
    stray.mkdir()
    write(tree_c, "src/c.py", "draft\n")
    capsys.readouterr()
    assert run(root, "clean") == 0
    assert capsys.readouterr().out.splitlines() == [
        f"recorded: {BRANCH_A}: merged, and the run that landed it stopped before it said so",
        f"removed: {tree_a}: its branch is merged",
        f"kept: {tree_c}: `{BRANCH_C}` is not merged",
        f"removed: {stray}: no work tree is registered there",
    ]
    assert tree_c.is_dir()
    assert not tree_a.exists()
    git(root, "update-ref", "-d", f"refs/heads/{BRANCH_C}")
    assert run(root, "clean") == 0
    assert capsys.readouterr().out.splitlines() == [
        kept_said(root, tree_c.name),
        f"removed: {tree_c}: its branch is gone",
    ]
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not drain.queue_home(root).exists()
    by = {entry.branch: entry for entry in drain.plan(offline(root)).tickets}
    assert by[BRANCH_A].state == "merged", "its branch never marked the ticket resolved"
    assert by[BRANCH_A].why == (
        f"`{BRANCH_A}` is merged, yet the ticket is still open: mark it resolved",
    )
    assert by[BRANCH_B].why == (
        f"blocked: waits on {ALPHA}, which this drain cannot merge (merged)",
    )


def test_clean_all_ends_an_interrupted_drain_and_keeps_its_uncommitted_work(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    for ref in (ALPHA, GAMMA):
        run(root, "open", ref)
    tree_a, tree_c = drain.tree_of(root, BRANCH_A), drain.tree_of(root, BRANCH_C)
    write(tree_a, "src/b.py", "half done\n")
    capsys.readouterr()
    assert run(root, "clean", "--all") == 0
    assert capsys.readouterr().out.splitlines() == [
        kept_said(root, BRANCH_A),
        f"removed: {tree_a}: the drain that made it was interrupted",
        f"removed: {tree_c}: the drain that made it was interrupted",
    ]
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert states(root) == {BRANCH_A: "buildable", BRANCH_C: "buildable", BRANCH_B: "waiting"}
    assert run(root, "clean", "--all") == 0, "a second clean finds nothing and says nothing"
    assert capsys.readouterr().out == ""
    assert restored_text(root, BRANCH_A, "src/b.py") == "half done\n"


def test_the_report_says_what_merged_what_waits_and_every_assumption(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        BETA: ticket(2, "Beta", "Blocked by: 05\n\n"),
        ".scratch/f/issues/04-delta.md": "# 04: Delta\n\nStatus: ready-for-agent\n",
        ".scratch/f/issues/05-eps.md": ticket(5, "Eps"),
    }
    root = project(tmp_path, **files)
    for ref in (ALPHA, GAMMA, ".scratch/f/issues/05-eps.md"):
        run(root, "open", ref)
    change = {"src/b.py": "B = 1\n", ALPHA: ticket(1, "Alpha", status="resolved")}
    build(root, BRANCH_A, change, record=approval("alpha"))
    monkeypatch.setattr(drain, "now", lambda: STAMP + timedelta(minutes=42))
    assert run(root, "land", ALPHA) == 0
    run(root, "close", GAMMA, "--outcome", "parked", "--reason", "open question: which?")
    build(root, "feature/5-eps", {"src/e.py": "E = 1\n"})
    assert run(root, "land", ".scratch/f/issues/05-eps.md") == 1
    capsys.readouterr()
    assert run(root, "report") == 0
    out = capsys.readouterr().out
    target = root / ".ratchet/queue/2026-11-12-101205.md"
    assert out.endswith(f"report: {target}\n")
    text = target.read_text(encoding="utf-8")
    assert out.startswith(text)
    assert text.splitlines() == [
        "# Queue drain, 2026-11-12 10:12",
        "",
        "## Merged",
        "",
        f"- `{ALPHA}` Alpha (`{BRANCH_A}`)",
        "",
        "## Waits for you",
        "",
        "- `.scratch/f/issues/05-eps.md` Eps (`feature/5-eps`)",
        (
            "  - behind: the branch does not hold the tip of `main` ("
            + git(root, "rev-parse", "main")[:12]
            + "), so its gates ran on a merge with an older one; bring it up to date and they rerun"
        ),
        (
            "  - record: no verdict record (docs/features/<slug>/verdict.json) is part of the"
            " change, so it is not the product of the pipeline"
        ),
        "",
        "## Parked",
        "",
        f"- `{GAMMA}` Gamma (`{BRANCH_C}`)",
        "  - open question: which?",
        "",
        "## Not built",
        "",
        f"- `{BETA}` Beta: refused",
        (
            "  - blocked: waits on .scratch/f/issues/05-eps.md, which this drain cannot merge"
            " (held): behind: the branch does not hold the tip of `main` ("
            + git(root, "rev-parse", "main")[:12]
            + "), so its gates ran on a merge with an older one; bring it up to date and they"
            " rerun"
        ),
        "- `.scratch/f/issues/04-delta.md` Delta: refused",
        (
            "  - conjecture: .scratch/f/issues/04-delta.md has no `## Conjecture` section and names"
            " no parent -> the owner adds `## Conjecture` to the ticket, or to its parent spec:"
            " `/ratchet:conjecture <ticket>` drafts it (the approach, the observation that would"
            " refute it, the alternatives and the criticism that kills each)"
        ),
        "",
        "## Assumptions",
        "",
        f"- `{ALPHA}`: Which case? Assumed: Lower. Refuter: A user types upper.",
        "",
        "## Records added",
        "",
        "- none",
        "",
        "## Timings",
        "",
        f"- `{ALPHA}`: 42 min in the queue; {UNSTAMPED}",
        f"- `{GAMMA}`: 42 min in the queue; {UNSTAMPED}",
        f"- `.scratch/f/issues/05-eps.md`: 42 min in the queue; {UNSTAMPED}",
    ]
    assert text.endswith("\n")
    assert git(root, "status", "--porcelain") == "", "the report is no commit's"
    assert all(note(root, b)["reported"] is True for b in (BRANCH_A, BRANCH_C, "feature/5-eps"))
    assert run(root, "report") == 0
    again = capsys.readouterr().out
    assert "## Merged\n\n- none\n" in again, "a ticket is reported once"
    assert f"## Parked\n\n- `{GAMMA}` Gamma (`{BRANCH_C}`)\n  - open question: which?\n" in again
    assert "- `.scratch/f/issues/05-eps.md` Eps (`feature/5-eps`)\n" in again, "left to the owner"


def test_the_report_leaves_out_a_ticket_still_building_and_times_what_it_can(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    capsys.readouterr()
    assert run(root, "report") == 0
    assert "Alpha" not in capsys.readouterr().out
    assert drain.minutes({"gates": {"a": 1.5, "b": 9, "c": 3.25, "d": 0.5, "e": "soon"}}) == (
        "slowest gates of its last check: b 9.0s, c 3.2s, a 1.5s"
    )
    assert drain.minutes({"opened": "yesterday"}) == "not timed"
    assert drain.minutes({"opened": "yesterday", "run_time": "run time: 9s"}) == "run time: 9s"


def test_what_is_not_the_expected_json_reads_as_nothing(tmp_path: Path) -> None:
    assert queue_notes.loaded("[1]") == queue_notes.loaded("{") == queue_notes.loaded(None) == {}
    assert queue_notes.texts("a") == queue_notes.tables({"a": 1}) == []
    assert queue_notes.texts(["a", 1]) == ["a"]
    assert drain.key("#4") == drain.key("4") == "4"
    assert drain.main(["drain", "plan", "--root", str(tmp_path / "nowhere")]) == 2
    assert drain.timings(tmp_path) == {}
    assert REAL_NOW().utcoffset() is not None, "a report's stamp is local time"


def test_what_a_branch_holds_is_read_from_its_commits(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert drain.change_of(root, "main", "feature/none") is None
    assert drain.change_of(root, "nope", "main") is None
    assert drain.assumptions(None) == drain.additions(None) == []
    assert drain.ahead(root, "main", "feature/none") is False
    run(root, "open", ALPHA)
    changed = {"docs/adr/0001-plain.md": "# Plain\n", "GLOSSARY.md": "# Rewritten\n"}
    build(root, BRANCH_A, changed)
    change = drain.change_of(root, "main", BRANCH_A)
    assert change is not None
    assert drain.assumptions(change) == []
    assert drain.additions(change) == ["docs/adr/0001-plain.md: ADR 0001 Plain"]
    git(root, "rm", "-q", "guards/manifest.toml")
    commit(root, "chore: no manifest")
    assert drain.additions(drain.change_of(root, "main", BRANCH_A)) == []


def planted(tmp_path: Path) -> Path:
    precious = tmp_path / "precious"
    write(precious, "kept/notes.txt", "the owner's\n")
    return precious


@pytest.mark.parametrize("level", ["user", "repository"])
def test_a_queue_directory_that_is_a_link_is_refused_and_what_it_points_at_is_kept(
    tmp_path: Path, capsys: Out, level: str
) -> None:
    root = project(tmp_path)
    precious = planted(tmp_path)
    home = drain.queue_home(root) if level == "repository" else drain.users_home()
    home.parent.mkdir(mode=0o700, exist_ok=True)
    home.symlink_to(precious, target_is_directory=True)
    for action in (["clean", "--all"], ["open", ALPHA], ["plan"]):
        capsys.readouterr()
        assert run(root, *action) == 2
        assert capsys.readouterr().out.startswith(f"drain: {home} is a link or a file, not a")
    assert (precious / "kept/notes.txt").read_text(encoding="utf-8") == "the owner's\n"
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_a_queue_directory_another_user_made_or_can_write_is_refused(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    home = drain.users_home()
    assert (home.stat().st_mode & 0o777, drain.queue_home(root).stat().st_mode & 0o777) == (
        0o700,
        0o700,
    )
    home.chmod(0o770)
    capsys.readouterr()
    assert run(root, "clean", "--all") == 2
    assert capsys.readouterr().out.startswith(f"drain: {home} can be written by other users")
    home.chmod(0o700)
    me = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: me)
    monkeypatch.setattr(drain, "users_home", lambda: home)
    monkeypatch.setattr(os, "getuid", lambda: me + 1)
    assert run(root, "clean", "--all") == 2
    assert capsys.readouterr().out.startswith(f"drain: {home} belongs to another user")
    assert drain.tree_of(root, BRANCH_A).is_dir()


def test_open_refuses_a_directory_that_became_another_s_while_it_was_made(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    answers = iter([None, f"{drain.users_home()} belongs to another user"])
    monkeypatch.setattr(drain, "foreign", lambda _root: next(answers))
    assert run(root, "open", ALPHA) == 1
    assert capsys.readouterr().out.startswith(f"open: {drain.users_home()} belongs to another")
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_clean_empties_only_registered_work_trees_and_follows_no_link(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert run(root, "open", GAMMA) == 0
    precious = planted(tmp_path)
    home = drain.queue_home(root)
    link, full, file = home / "a-link", home / "not-a-tree", home / "stray.txt"
    link.symlink_to(precious, target_is_directory=True)
    write(full, "left/by.txt", "somebody\n")
    file.write_text("x\n", encoding="utf-8")
    tree_c = drain.tree_of(root, BRANCH_C)
    git(root, "worktree", "remove", "--force", str(tree_c))
    tree_c.symlink_to(precious, target_is_directory=True)
    git(root, "worktree", "add", "-q", "--detach", str(drain.tree_of(root, BRANCH_A) / "base"))
    capsys.readouterr()
    assert run(root, "clean") == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"left: {link}: no work tree is registered there, and {drain.NOT_A_TREE}",
        f"kept: {drain.tree_of(root, BRANCH_A)}: `{BRANCH_A}` is not merged",
        f"left: {tree_c}: no work tree is registered there, and {drain.NOT_A_TREE}",
        f"left: {full}: no work tree is registered there, and {drain.NOT_A_TREE}",
        f"left: {file}: no work tree is registered there, and {drain.NOT_A_TREE}",
    ]
    assert (precious / "kept/notes.txt").read_text(encoding="utf-8") == "the owner's\n"
    assert (full / "left/by.txt").is_file()
    assert (drain.tree_of(root, BRANCH_A) / "base/.git").is_file(), "a nested tree is its tree's"
    assert run(root, "clean", "--all") == 0
    assert not drain.tree_of(root, BRANCH_A).exists()
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_a_registered_work_tree_replaced_by_a_link_is_left_where_it_points(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    precious = planted(tmp_path)
    tree = drain.tree_of(root, BRANCH_A)
    moved = tmp_path / "moved"
    tree.rename(moved)
    tree.symlink_to(precious, target_is_directory=True)
    capsys.readouterr()
    assert run(root, "clean", "--all") == 0
    assert capsys.readouterr().out == (
        f"left: {tree}: the drain that made it was interrupted, and {drain.NOT_A_TREE}\n"
    )
    assert (precious / "kept/notes.txt").read_text(encoding="utf-8") == "the owner's\n"


def test_a_removed_work_tree_takes_what_ran_from_it_along(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    service = servers.sleeper(tree)
    try:
        capsys.readouterr()
        assert run(root, "clean", "--all", "--take-over") == 0
        assert capsys.readouterr().out.splitlines()[0] == (
            f"ended: process {service.pid}, which ran a program of {tree}"
        )
        assert service.wait(timeout=5) == -signal.SIGKILL
    finally:
        service.kill()
        service.wait()


OWED_BINDING = (
    "\n- Life events: none\n\n## Gates\n\n"
    "| Question | Tool | Tier | Threshold source | Entry point |\n| --- | --- | --- | --- | --- |\n"
    "| survives failing dependencies | later: code:outbound-call | 1 | | `just faults` |\n\n"
    "## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n"
    "| outbound-call | `src/**/*.py` | `requests\\.get\\(` |\n"
)


def test_a_ticket_red_on_an_owed_safeguard_is_held_until_the_safeguard_is_bound(
    tmp_path: Path, capsys: Out
) -> None:
    files = {
        "docs/process/binding.md": binding(MERGE) + OWED_BINDING,
        "src/b.py": "requests.get(1)\n",
    }
    root = project(tmp_path, **files)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    write(tree, "src/b.py", "requests.get(1)\nrequests.get(2)\n")
    capsys.readouterr()
    assert triggers.main(["triggers", "--root", str(tree)]) == 1
    printed = capsys.readouterr().out
    git(tree, "checkout", "--", "src/b.py")
    result = tmp_path / "result.json"
    output = f"{printed}{printed}check: red"
    result.write_text(json.dumps({"verdict": "request_changes", "checkOutput": output}))
    assert run(root, "close", GAMMA, "--outcome", "parked", "--result", str(result)) == 0
    assert note(root, BRANCH_C)["reasons"] == [
        (
            "safeguard owed: `outbound-call` gained an instance while a row it fires is still"
            " `later:`; run /ratchet:safeguard on the default branch, and the drain builds this"
            " ticket again"
        )
    ]
    assert note(root, BRANCH_C)["owes"] == ["outbound-call"]
    assert states(root)[BRANCH_C] == "held"
    capsys.readouterr()
    assert run(root, "report") == 0
    assert "run /ratchet:safeguard on the default branch" in capsys.readouterr().out
    bound = OWED_BINDING.replace("later: code:outbound-call", "a timeout lint")
    write(root, "docs/process/binding.md", binding(MERGE) + bound)
    commit(root, "chore(method): safeguard outbound calls")
    assert states(root)[BRANCH_C] == "buildable", "nothing is owed, so the hold is over"


def test_a_ticket_parked_after_it_has_commits_is_held_until_its_text_changes(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    reason = "open question (direction): A or B?"
    run(root, "close", ALPHA, "--outcome", "parked", "--reason", reason)
    entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
    assert (entry.state, entry.why) == ("held", (reason,))
    write(root, ALPHA, ticket(1, "Alpha") + "\n## Answers\n\nA or B? A.\n")
    commit(root, "docs(tracker): the owner's answer")
    assert states(root)[BRANCH_A] == "built", "the answer changed the ticket: it is judged again"
    capsys.readouterr()


def test_a_dependent_of_a_ticket_left_to_the_owner_says_what_it_waits_on(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    assert run(root, "land", ALPHA) == 1
    by = {e.ref: e for e in drain.plan(offline(root)).tickets}
    assert by[ALPHA].state == "held"
    assert by[BETA].state == "refused"
    (why,) = by[BETA].why
    assert why.startswith(
        f"blocked: waits on {drain.key(ALPHA)}, which this drain cannot merge (held): record:"
    )
    capsys.readouterr()


def test_update_says_why_it_cannot_make_the_work_tree_of_a_built_branch(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    git(root, "worktree", "remove", "--force", str(drain.tree_of(root, BRANCH_A)))
    moved(root)
    git(root, "worktree", "add", "--quiet", str(tmp_path / "elsewhere"), BRANCH_A)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 1
    assert "no work tree of `feature/1-alpha` could be made" in capsys.readouterr().out
    git(root, "worktree", "remove", "--force", str(tmp_path / "elsewhere"))
    answers = iter([None, "a directory became another user's"])
    monkeypatch.setattr(drain, "foreign", lambda _root: next(answers))
    assert run(root, "update", ALPHA) == 1
    assert "became another user's" in capsys.readouterr().out


def test_a_ticket_left_waiting_for_a_reason_the_drain_resolves_is_retried_not_held(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    run(root, "open", GAMMA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"}, record=approval("alpha"))
    git(root, "worktree", "remove", "--force", str(drain.tree_of(root, BRANCH_A)))
    build(
        root,
        BRANCH_C,
        {"src/c.py": "C = 1\n", GAMMA: ticket(3, "Gamma", status="resolved")},
        record=approval("gamma"),
    )
    assert run(root, "land", GAMMA) == 0
    assert run(root, "land", ALPHA) == 1
    kept = note(root, BRANCH_A)
    assert (kept["outcome"], kept["hold"], kept["attempts"]) == ("waits", False, 1)
    entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
    assert (entry.state, entry.why) == ("built", (drain.BEHIND,))
    capsys.readouterr()


def test_a_mechanical_reason_that_keeps_coming_back_becomes_the_owners(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    red = "gates: `just check` is red on the merge result (exit 1)"
    for attempt in range(1, queue_notes.RETRIES):
        drain.close(
            root,
            drain.preflight.fetch(ALPHA, root, offline(root).tracker),
            outcome="waits",
            reasons=[red],
        )
        assert note(root, BRANCH_A)["attempts"] == attempt
        assert states(root)[BRANCH_A] == "built"
    drain.close(
        root,
        drain.preflight.fetch(ALPHA, root, offline(root).tracker),
        outcome="waits",
        reasons=[red],
    )
    entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
    assert entry.state == "held"
    assert entry.why[0].startswith(f"retried {queue_notes.RETRIES} times")
    assert entry.why[1] == red
    drain.close(
        root,
        drain.preflight.fetch(ALPHA, root, offline(root).tracker),
        outcome="waits",
        reasons=["record: no verdict"],
    )
    assert note(root, BRANCH_A)["attempts"] == 0
    capsys.readouterr()


def test_a_mechanical_reason_is_retried_twice_and_the_third_time_it_is_the_owners(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    red = "gates: `just check` is red on the merge result (exit 1)"
    for _ in range(2):
        drain.close(
            root,
            drain.preflight.fetch(ALPHA, root, offline(root).tracker),
            outcome="waits",
            reasons=[red],
        )
        assert states(root)[BRANCH_A] == "built"
    drain.close(
        root,
        drain.preflight.fetch(ALPHA, root, offline(root).tracker),
        outcome="waits",
        reasons=[red],
    )
    entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
    assert entry.state == "held"
    capsys.readouterr()


def test_a_run_that_keeps_erroring_is_retried_and_then_the_owners(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    crashed = "errored: the pipeline run errored twice: implement agent returned no result"
    assert queue_notes.mechanical([crashed])
    for attempt in range(1, queue_notes.RETRIES):
        drain.close(
            root,
            drain.preflight.fetch(ALPHA, root, offline(root).tracker),
            outcome="waits",
            reasons=[crashed],
        )
        assert note(root, BRANCH_A)["attempts"] == attempt
        assert note(root, BRANCH_A)["hold"] is False
        entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
        assert entry.state == "buildable", "no commits: the next drain builds it from the start"
    drain.close(
        root,
        drain.preflight.fetch(ALPHA, root, offline(root).tracker),
        outcome="waits",
        reasons=[crashed],
    )
    entry = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_A)
    assert entry.state == "held"
    assert entry.why[0].startswith(f"the pipeline run errored on each of {queue_notes.RETRIES}")
    assert entry.why[1] == crashed
    capsys.readouterr()


PROMISES = "\n## Acceptance criteria\n\n- [ ] It saves a link.\n- [ ] It lists the links.\n"
WHOLE = {ALPHA: ticket(1, "Alpha", "") + PROMISES}
CHILD = ".scratch/f/issues/04-child.md"

HALVES: list[dict[str, object]] = [
    {"title": "Save", "promises": ["It saves a link."], "summary": "Saving."},
    {"title": "List", "promises": ["It lists the links."], "summary": "Listing."},
]


def plan_file(tmp_path: Path, parts: list[dict[str, object]]) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"parts": parts}), encoding="utf-8")
    return path


def test_split_files_the_parts_with_the_label_and_closes_the_parent_as_split(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert len(children) == 2
    first, second = ((root / child).read_text(encoding="utf-8") for child in children)
    assert "ready-for-agent" in first
    assert f"Split from: {ALPHA}" in first
    assert "- [ ] It saves a link." in first
    assert f"Blocked by: {Path(children[0]).name}" in second
    assert note(root, BRANCH_A)["outcome"] == "split"
    assert (root / ALPHA).read_text(encoding="utf-8") == WHOLE[ALPHA], "the parent is never edited"


ISSUES = ".scratch/f/issues"
FILED = ["01-a.md", "02-b.md", "03-c.md", "04-save.md", "05-list.md"]


def landed_parts(root: Path, children: list[str]) -> None:
    assert git(root, "status", "--porcelain") == "", "the split leaves the checkout clean"
    assert sorted(path.name for path in (root / ISSUES).iterdir()) == FILED, "no part twice"
    for child in children:
        commits = git(root, "rev-list", "main", "--", child).splitlines()
        assert len(commits) == 1, f"{child} is on the default branch, committed once"
    assert git(root, "branch", "--list", "records/*") == "", "no records branch is left"
    drafted = drain.plan(offline(root))
    assert drafted.blocked == (), "the checkout is fit"
    assert children[0] in drafted.start, "the plan starts the first part"


def test_a_local_split_lands_its_parts_so_the_plan_starts_them(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    landed_parts(root, children)


class Killed(BaseException): ...


def killed_after(real: object, when: object = None) -> object:
    def run(*args: object, **kwargs: object) -> object:
        done = real(*args, **kwargs)  # type: ignore[operator]
        if when is None or args[1] == when:
            raise Killed
        return done

    return run


KILLS = {
    "the parts written": (drain.split, "file_locally", None),
    "the note written": (drain, "close", None),
    "the parts committed aside": (drain, "commit_aside", None),
    "the scratch removed before the merge": (drain, "commit_parts", None),
    "the merge landed and its branch kept": (drain.merge, "merge", None),
}


@pytest.mark.parametrize("kill", KILLS.values(), ids=KILLS.keys())
def test_a_local_split_killed_half_way_and_run_again_files_and_lands_each_part_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kill: tuple[object, str, str]
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    module, name, when = kill
    with monkeypatch.context() as cut:
        cut.setattr(module, name, killed_after(getattr(module, name), when))
        with pytest.raises(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    children = note(root, BRANCH_A)["children"]
    assert isinstance(children, list)
    landed_parts(root, children)


def test_a_split_killed_with_its_scratch_work_tree_out_removes_it_and_lands_when_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    real = drain.gatelib.git

    def dying(where: Path, *args: str) -> str | None:
        if args[:2] == ("worktree", "remove") and drain.merge.SCRATCH in args[-1]:
            raise Killed
        return real(where, *args)

    with monkeypatch.context() as cut:
        cut.setattr(drain.gatelib, "git", dying)
        with pytest.raises(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    printed = capsys.readouterr().out.splitlines()
    assert any(line.startswith("merge: removed the work tree ") for line in printed), printed
    children = note(root, BRANCH_A)["children"]
    assert isinstance(children, list)
    landed_parts(root, children)


def test_a_local_split_the_merge_refuses_is_closed_exits_4_and_lands_when_run_again(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE, **{"docs/process/binding.md": binding(MERGE, "false")})
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out
    assert "`false` is red on the merge result" in printed
    children = json.loads(printed.splitlines()[-1])["children"]
    assert note(root, BRANCH_A)["outcome"] == "split"
    write(root, "docs/process/binding.md", binding(MERGE))
    commit(root, "the check is green again")
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    landed_parts(root, children)


def test_a_local_split_with_a_forge_names_the_pull_request_and_commits_nothing(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "remote", "add", "origin", str(tmp_path / "forge.git"))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    printed = capsys.readouterr().out.splitlines()
    children = json.loads(printed[-1])["children"]
    assert printed[-2] == (
        "next: this repository has a forge (`origin`): commit "
        + ", ".join(children)
        + " on a branch of their own and open the pull request; its merge job merges records"
        " alone, and the plan lists the parts once the default branch holds them"
    )
    assert git(root, "rev-list", "main", "--", *children) == "", "nothing is committed"
    assert git(root, "branch", "--list", "records/*") == "", "no records branch is made"


LANDING = "records/split-1-alpha"
OTHER = f"{ISSUES}/04-other.md"


def red_split(tmp_path: Path, capsys: Out) -> tuple[Path, str, str]:
    root = project(tmp_path, **WHOLE, **{"docs/process/binding.md": binding(MERGE, "false")})
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    return root, plan, capsys.readouterr().out


def green_check(root: Path, *, green: bool = True) -> None:
    write(root, "docs/process/binding.md", binding(MERGE, "true" if green else "false"))
    git(root, "commit", "-q", "--allow-empty", "-m", "the check", "--", "docs/process/binding.md")


def test_a_split_that_did_not_land_names_the_branch_holding_its_parts_and_the_command_again(
    tmp_path: Path, capsys: Out
) -> None:
    root, plan, printed = red_split(tmp_path, capsys)
    assert (
        f"not landed: the parts are on `{LANDING}`, and the checkout does not hold them; once the"
        f" cause above is gone, run `drain.py split {ALPHA} --plan {plan}` again"
    ) in printed.splitlines()
    assert note(root, BRANCH_A)["landing"] == LANDING, "the split records the branch it made"
    assert note(root, BRANCH_A)["committed"] == git(root, "rev-parse", LANDING), "and its commit"
    children = json.loads(printed.splitlines()[-1])["children"]
    assert git(root, "ls-tree", "--name-only", LANDING, "--", *children).splitlines() == children
    assert not any((root / child).exists() for child in children)


def test_a_rerun_after_the_owner_filed_a_ticket_lands_each_part_once_under_a_free_number(
    tmp_path: Path, capsys: Out
) -> None:
    root, plan, _ = red_split(tmp_path, capsys)
    write(root, "docs/process/binding.md", binding(MERGE))
    write(root, OTHER, ticket(4, "Other"))
    commit(root, "the check is green again, and the owner files a ticket")
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert children == [f"{ISSUES}/05-save.md", f"{ISSUES}/06-list.md"], "past the owner's 04"
    assert note(root, BRANCH_A)["children"] == children
    assert git(root, "branch", "--list", "records/*") == "", "no branch of the split is left"
    assert git(root, "status", "--porcelain") == "", "the checkout is fit"
    on_main = git(root, "ls-tree", "--name-only", "main", "--", f"{ISSUES}/").splitlines()
    assert on_main == [ALPHA, BETA, GAMMA, OTHER, *children], "each part once"
    assert "Blocked by: 05-save.md" in (root / children[1]).read_text(encoding="utf-8")
    assert drain.plan(offline(root)).blocked == ()


def test_a_part_the_owner_edited_after_a_kill_is_still_that_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    with monkeypatch.context() as cut:
        cut.setattr(drain, "close", killed_after(drain.close))
        with pytest.raises(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    first = root / ISSUES / "04-save.md"
    edited = first.read_text(encoding="utf-8").replace("Saving.", "Saving, with a date.")
    first.write_text(edited, encoding="utf-8")
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    landed_parts(root, [f"{ISSUES}/04-save.md", f"{ISSUES}/05-list.md"])
    assert git(root, "show", f"main:{ISSUES}/04-save.md") == edited.strip()


def test_a_rerun_leaves_its_own_branch_that_holds_more_than_the_parts(
    tmp_path: Path, capsys: Out
) -> None:
    root, plan, _ = red_split(tmp_path, capsys)
    git(root, "switch", "-q", LANDING)
    write(root, "docs/notes.md", "mine\n")
    git(root, "add", "--", "docs/notes.md")
    git(root, "commit", "-q", "-m", "my notes")
    held = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    green_check(root)
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    assert (
        f"not landed: `{LANDING}` holds other than just the parts this split writes, so it stays"
        f" as it is: take what is yours from it, delete it, then run `drain.py split {ALPHA}"
        f" --plan {plan}` again"
    ) in printed
    assert git(root, "rev-parse", LANDING) == held
    assert git(root, "status", "--porcelain") == "", "no part is filed a second time"


def test_a_rerun_with_another_plan_leaves_the_branch_holding_the_parts(
    tmp_path: Path, capsys: Out
) -> None:
    root, _, _ = red_split(tmp_path, capsys)
    held = git(root, "rev-parse", LANDING)
    green_check(root)
    plan = str(plan_file(tmp_path, HALVES[:1]))
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert (
        f"not landed: `{LANDING}` holds other than just the parts this split writes, so it stays"
        " as it is: take what is yours from it, delete it"
    ) in capsys.readouterr().out
    assert git(root, "rev-parse", LANDING) == held
    assert note(root, BRANCH_A)["outcome"] == "split", "the split is not parked"


def test_a_rerun_leaves_its_branch_while_a_work_tree_has_it_checked_out(
    tmp_path: Path, capsys: Out
) -> None:
    root, plan, _ = red_split(tmp_path, capsys)
    git(root, "worktree", "add", "-q", str(tmp_path / "theirs"), LANDING)
    green_check(root)
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert (
        f"not landed: `{LANDING}` is checked out in a work tree, so it stays as it is: switch that"
        f" work tree to another branch, then run `drain.py split {ALPHA} --plan {plan}` again"
    ) in capsys.readouterr().out.splitlines()
    assert git(root, "branch", "--list", LANDING) != ""


def test_a_split_off_the_default_branch_leaves_its_parts_in_the_checkout_and_says_so(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "switch", "-q", "-c", "elsewhere")
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    assert "merge: the checkout is on `elsewhere`, and the parts land from `main`" in printed
    assert (
        "not landed: the parts stay uncommitted in the checkout; once the cause above is gone,"
        f" run `drain.py split {ALPHA} --plan {plan}` again"
    ) in printed
    children = json.loads(printed[-1])["children"]
    assert git(root, "status", "--porcelain").splitlines() == [f"?? {c}" for c in children]


def test_a_ticket_waiting_on_a_split_whose_parts_did_not_land_waits_on_them(
    tmp_path: Path, capsys: Out
) -> None:
    root, _, printed = red_split(tmp_path, capsys)
    children = json.loads(printed.splitlines()[-1])["children"]
    beta = next(e for e in drain.plan(offline(root)).tickets if e.ref == BETA)
    assert (beta.state, beta.waits_on) == ("waiting", tuple(children)), beta.why


def test_with_a_forge_a_ticket_waits_on_the_parts_until_their_pull_request_merges(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "remote", "add", "origin", str(tmp_path / "forge.git"))
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    git(root, "switch", "-q", "-c", "split-parts")
    git(root, "add", "--", *children)
    git(root, "commit", "-q", "-m", "docs(tracker): the parts")
    git(root, "switch", "-q", "main")
    beta = next(e for e in drain.plan(offline(root)).tickets if e.ref == BETA)
    assert (beta.state, beta.waits_on) == ("waiting", tuple(children)), beta.why


def test_a_local_split_lands_its_parts_and_no_other_uncommitted_record(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    draft = f"{ISSUES}/09-draft.md"
    write(root, draft, "# 09: half a thought\n")
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines() == children
    assert git(root, "ls-tree", "--name-only", "main", "--", draft) == "", "the draft stays"
    assert git(root, "status", "--porcelain") == f"?? {draft}"


PARTS = f":(exclude,glob){ISSUES}/*-save.md", f":(exclude,glob){ISSUES}/*-list.md"


def owners_work(root: Path) -> tuple[str, str]:
    return (
        git(root, "diff", "--cached", "--raw", "--no-abbrev", "--", ".", *PARTS),
        git(root, "status", "--porcelain", "--untracked-files=all", "--", ".", *PARTS),
    )


def split_commits(root: Path) -> list[list[str]]:
    made = git(root, "rev-list", "--branches", "-F", f"--grep={drain.merge.SUBJECT}")
    return [
        git(root, "diff-tree", "-r", "--no-renames", "--name-only", "--no-commit-id", sha).split()
        for sha in made.split()
    ]


def stage(root: Path, what: str) -> None:
    if what == "record":
        write(root, f"{ISSUES}/09-draft.md", "# 09: half a thought\n")
        git(root, "add", "--", f"{ISSUES}/09-draft.md")
    elif what == "source":
        write(root, "src/a.py", "A = 2\n")
        git(root, "add", "--", "src/a.py")
    else:
        git(root, "mv", GAMMA, f"{ISSUES}/03-gamma.md")


@pytest.mark.parametrize("staged", ["record", "source", "rename"])
def test_a_local_split_commits_its_parts_alone_and_leaves_what_the_owner_staged(
    tmp_path: Path, capsys: Out, staged: str
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    stage(root, staged)
    before = owners_work(root)
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert split_commits(root) == [children], "the split's commit holds its parts alone"
    assert git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines() == children
    assert owners_work(root) == before, "what the owner staged stays staged, as it was"


def test_a_commit_hook_that_stages_another_file_keeps_the_branch_holding_it_and_says_so(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    hooked = hook_script(
        root / ".git/hooks", "pre-commit", "echo hooked > docs/hooked.md\ngit add docs/hooked.md"
    )
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    assert (
        f"not landed: `{LANDING}` holds what the commit hooks made besides the parts, so it stays"
        " as it is: take what is yours from it, delete it; the parts stay uncommitted in the"
        f" checkout; once the cause above is gone, run `drain.py split {ALPHA} --plan {plan}` again"
    ) in printed
    children = json.loads(printed[-1])["children"]
    held = git(root, "ls-tree", "-r", "--name-only", LANDING, "--", "docs/hooked.md", *children)
    assert sorted(held.splitlines()) == sorted(["docs/hooked.md", *children]), "the hook's commit"
    assert git(root, "status", "--porcelain", "--", *children).splitlines() == [
        f"?? {child}" for child in children
    ]
    hooked.unlink()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert f"not landed: `{LANDING}` holds other than just the parts" in capsys.readouterr().out


def test_a_commit_hook_that_writes_a_file_and_reads_an_ignored_folder_lets_the_parts_land(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE, **{".gitignore": "tools/\n"})
    write(root, "tools/lint", "#!/bin/sh\necho hooked > docs/hooked.md\n")
    (root / "tools/lint").chmod(0o755)
    ran = tmp_path / "ran"
    hooks = ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit")
    for name in hooks:
        hook_script(root / ".git/hooks", name, f'echo {name} "$(pwd)" >> {ran}')
    hook_script(
        root / ".git/hooks", "pre-commit", f'echo pre-commit "$(pwd)" >> {ran}\n./tools/lint'
    )
    run(root, "open", ALPHA)
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert ran.read_text(encoding="utf-8").splitlines()[:4] == [
        f"{name} {root}" for name in hooks
    ], "every commit hook runs, in the checkout, with its ignored folders"
    assert git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines() == children
    assert git(root, "rev-list", "--branches", "--", "docs/hooked.md") == "", "in no commit"
    assert git(root, "status", "--porcelain") == "?? docs/hooked.md"


STAGED_WORK = {"src/old.py": "O = 1\n", "src/gone.py": "G = 1\n", "src/partial.py": "P = 1\n"}
DESTROYING = {"clean": "git clean -fdq", "checkout": "git checkout -- .", "stash": "git stash -q"}
RESTORING = {
    "stash and pop": "git stash push -q --keep-index && git stash pop -q",
    "patch and apply": 'git diff --binary > "$TMPDIR/p.patch" && git checkout -- .'
    ' && git apply "$TMPDIR/p.patch"',
}
LINK = "src/link"
HARMED = re.compile(
    r"records: a commit hook or another process (deleted|changed|restored) `([^`]+)` as the parts"
    r" were committed(?:; the bytes it had are object ([0-9a-f]+)(, which the split wrote)?)?"
)
WRITTEN = {"src/partial.py", "src/intent.py"}


def stage_everything(root: Path) -> None:
    write(root, "src/added.py", "N = 1\n")
    git(root, "add", "--", "src/added.py")
    (root / LINK).symlink_to("added.py")
    git(root, "add", "--", LINK)
    git(root, "mv", "src/old.py", "src/moved.py")
    git(root, "rm", "-q", "--", "src/gone.py")
    write(root, "src/intent.py", "I = 1\n")
    git(root, "add", "-N", "--", "src/intent.py")
    write(root, "src/partial.py", "P = 2\n")
    git(root, "add", "--", "src/partial.py")
    write(root, "src/partial.py", "P = 3\n")


def sources(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in (root / "src").rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("hook", DESTROYING)
def test_a_commit_hook_that_destroys_the_owners_work_names_each_file_and_the_object_holding_it(
    tmp_path: Path, capsys: Out, hook: str
) -> None:
    root = project(tmp_path, **WHOLE, **STAGED_WORK)
    run(root, "open", ALPHA)
    stage_everything(root)
    before = sources(root)
    hooked = hook_script(root / ".git/hooks", "pre-commit", DESTROYING[hook])
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    after = sources(root)
    hurt = {path for path in {*before, *after} if before.get(path) != after.get(path)}
    named = {match[2]: match for line in printed if (match := HARMED.match(line))}
    assert hurt, "the hook reached the owner's work"
    assert set(named) == hurt, printed
    for path, match in named.items():
        what, held = match[1], match[3]
        happened = "deleted" if path not in after else "changed"
        assert what == ("restored" if path not in before else happened), path
        if path in before and path != LINK:
            assert raw(root, "cat-file", "blob", held) == before[path], f"{path} is in {held}"
            assert bool(match[4]) == (path in WRITTEN), f"{path}: {match[0]}"
    if LINK in hurt:
        assert (
            f"records: a commit hook or another process deleted `{LINK}` as the parts were"
            " committed; it was a link to `added.py`"
        ) in printed
    again = f"once the cause above is gone, run `drain.py split {ALPHA} --plan {plan}` again"
    hooked.unlink()
    if hook == "stash":
        stash = raw(root, "rev-parse", "refs/stash").strip()
        moved = f"records: the commit hooks moved the stash: `stash@{{0}}` is now {stash}"
        assert any(
            line.startswith(moved) and line.endswith(f": `git stash show -p {stash}` prints them")
            for line in printed
        ), printed
        assert (
            "not landed: the commit hooks took the parts out of the commit, so"
            f" `{LANDING}` holds a commit that adds nothing, and it stays as it is: delete it;"
            f" {again}"
        ) in printed
        held = git(root, "rev-parse", LANDING)
        assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
        assert f"not landed: `{LANDING}` holds other than just the parts" in capsys.readouterr().out
        assert git(root, "rev-parse", LANDING) == held, "the rerun keeps the empty commit"
        return
    assert (
        "not landed: what the owner staged changed in the checkout as the parts were committed,"
        f" as the lines above say, so nothing is merged: `{LANDING}` holds the parts, and they"
        f" stay uncommitted in the checkout; {again}"
    ) in printed
    assert {
        f"{match[0]} and no ref holds, so git's collection removes it once its grace period is"
        f" over: `git cat-file blob {match[3]}` prints them"
        for path, match in named.items()
        if path in WRITTEN
    } & set(printed), printed
    assert run(root, "split", ALPHA, "--plan", plan) == 0, "the rerun commits the parts again"
    children = note(root, BRANCH_A)["children"]
    assert isinstance(children, list)
    assert git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines() == children
    assert git(root, "branch", "--list", "records/*") == ""


@pytest.mark.parametrize("hook", RESTORING)
def test_a_commit_hook_that_sets_the_checkout_aside_and_back_lets_the_parts_land(
    tmp_path: Path, capsys: Out, hook: str
) -> None:
    root = project(tmp_path, **WHOLE, **STAGED_WORK)
    run(root, "open", ALPHA)
    stage_everything(root)
    before = owners_view(root)
    hook_script(root / ".git/hooks", "pre-commit", RESTORING[hook])
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines() == children
    assert changed(before, owners_view(root)) == {}, "the owner's work is as it was"


def subcommand(args: list[str]) -> str:
    rest = args[3:] if args[1] == "-C" else args[1:]
    return next(arg for arg in rest if not arg.startswith("-"))


def test_a_split_with_nothing_staged_lists_the_index_in_one_git_call_and_reads_no_other_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    (root / "data").mkdir()
    (root / "data/big.bin").write_bytes(os.urandom(4 << 20))
    big = git(root, "hash-object", "data/big.bin")
    calls: list[str] = []
    real_commit, real_run = drain.commit_parts, subprocess.run

    def running(args: object, *rest: object, **kwargs: object) -> object:
        if isinstance(args, list) and args[:1] == ["git"]:
            calls.append(subcommand(args))
        return real_run(args, *rest, **kwargs)  # type: ignore[call-overload]

    def counted(*args: object, **kwargs: object) -> object:
        with pytest.MonkeyPatch.context() as cut:
            cut.setattr(subprocess, "run", running)
            return real_commit(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(drain, "commit_parts", counted)
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    assert calls == [
        "worktree",
        "rev-parse",
        "read-tree",
        "add",
        "diff-index",
        "commit",
        "worktree",
        "worktree",
        "for-each-ref",
        "hash-object",
        "rev-list",
        "diff",
        "diff",
    ], "the split lists the owner's index once, and reads and writes nothing more for it"
    assert raw(root, "cat-file", "-t", big) == "", "an untracked file is not written as an object"


def test_a_split_lands_over_files_it_cannot_read_or_name_and_a_log_another_process_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    write(root, "src/secret.key", "K = 1\n")
    write(root, "src/a.py", "A = 2\n")
    write(root, "src/a\nb.py", "N = 1\n")
    write(root, "src/gone.py", "G = 1\n")
    write(root, "src/locked.py", "L = 1\n")
    git(root, "add", "--", "src/gone.py", "src/locked.py")
    write(root, "src/locked.py", "L = 2\n")
    for name in ("src/secret.key", "src/a.py", "src/locked.py"):
        (root / name).chmod(0)
    (root / "logs").mkdir()
    stop = threading.Event()

    def server() -> None:
        with (root / "logs/app.log").open("a", encoding="utf-8") as log:
            while not stop.is_set():
                log.write("a line a running server wrote\n")
                log.flush()

    calls: list[object] = []
    real_run = subprocess.run

    def gone_once_listed(args: object, *rest: object, **kwargs: object) -> object:
        done = real_run(args, *rest, **kwargs)  # type: ignore[call-overload]
        if calls and isinstance(calls[-1], list) and "--pathspec-from-file=-" in calls[-1]:
            (root / "src/gone.py").unlink()
        calls.append(args)
        return done

    writer = threading.Thread(target=server)
    capsys.readouterr()
    writer.start()
    try:
        with monkeypatch.context() as cut:
            cut.setattr(subprocess, "run", gone_once_listed)
            code = run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES)))
    finally:
        stop.set()
        writer.join()
    printed = capsys.readouterr().out.splitlines()
    assert code == 0, printed
    assert "records: not saved: src/locked.py: Permission denied" in printed
    children = json.loads(printed[-1])["children"]
    assert git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines() == children


def test_a_staged_file_another_process_changes_as_the_parts_are_committed_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE, **{"logs/kept.log": "start\n"})
    run(root, "open", ALPHA)
    write(root, "logs/kept.log", "start\nstaged\n")
    write(root, "src/locked.py", "L = 1\n")
    git(root, "add", "--", "logs/kept.log", "src/locked.py")
    (root / "src/locked.py").chmod(0)
    held = git(root, "rev-parse", ":logs/kept.log")
    real_run = subprocess.run

    def writing(args: object, *rest: object, **kwargs: object) -> object:
        if isinstance(args, list) and "commit" in args:
            with (root / "logs/kept.log").open("a", encoding="utf-8") as log:
                log.write("a line a running server wrote\n")
            (root / "src/locked.py").unlink(missing_ok=True)
        return real_run(args, *rest, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", writing)
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    by = "records: a commit hook or another process"
    assert printed[1:4] == [
        "records: not saved: src/locked.py: Permission denied",
        (
            f"{by} changed `logs/kept.log` as the parts were committed; the bytes it had are"
            f" object {held}: `git cat-file blob {held}` prints them"
        ),
        (
            f"{by} deleted `src/locked.py` as the parts were committed; the split could not read"
            " it, so it was not saved"
        ),
    ], printed


def test_a_split_on_a_detached_head_says_so_and_what_to_do(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "switch", "-q", "--detach", "main")
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == drain.NOT_LANDED
    assert (
        "merge: the checkout's HEAD is detached, and the parts land from `main`: switch it to"
        " `main`"
    ) in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize(
    ("body", "code"), [("exit 3", 1), ("git checkout -q -b hooked", 0)], ids=["refuses", "moves"]
)
def test_a_commit_hook_that_refuses_and_prints_nothing_is_named_with_the_exit(
    tmp_path: Path, capsys: Out, *, body: str, code: int
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    hook_script(root / ".git/hooks", "pre-commit", body)
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == drain.NOT_LANDED
    assert (
        f"records: the parts were not committed: `git commit` exited {code} and printed nothing:"
        f" a commit hook refused them, or moved the commit off `{LANDING}`"
    ) in capsys.readouterr().out.splitlines()


def test_a_split_that_meets_a_held_index_lock_names_it_and_lands_once_it_is_gone(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    lock = root / ".git/index.lock"
    lock.write_text("", encoding="utf-8")
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    assert (
        f"not landed: the parts are on `{LANDING}`, and `{lock}` locks the checkout's index, so the"
        " merge could not move it: once no git command runs in this repository, delete that file;"
        f" once the cause above is gone, run `drain.py split {ALPHA} --plan {plan}` again"
    ) in printed
    assert not [line for line in printed if "moved meanwhile" in line], "the lock is the cause"
    lock.unlink()
    assert run(root, "split", ALPHA, "--plan", plan) == 0


def test_a_ticket_waiting_on_parts_no_branch_holds_any_more_is_refused_and_says_why(
    tmp_path: Path, capsys: Out
) -> None:
    root, _, printed = red_split(tmp_path, capsys)
    children = json.loads(printed.splitlines()[-1])["children"]
    git(root, "branch", "-q", "-D", LANDING)
    beta = next(e for e in drain.plan(offline(root)).tickets if e.ref == BETA)
    assert (beta.state, beta.why) == (
        "refused",
        tuple(
            f"blocked: blocker {ALPHA} was split, and its part {child} is not done, and no branch"
            " holds it, so it cannot land"
            for child in children
        ),
    )


def test_a_landing_branch_that_moves_as_the_split_lets_it_go_is_kept_and_the_split_says_so(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plan, _ = red_split(tmp_path, capsys)
    green_check(root)
    real, moved = drain.gatelib.git, []

    def racing(where: Path, *args: str) -> str | None:
        if args[:2] == ("update-ref", "-d") and not moved:
            tree = real(where, "rev-parse", f"{LANDING}^{{tree}}") or ""
            moved.append(real(where, "commit-tree", tree, "-p", LANDING, "-m", "theirs") or "")
            real(where, "update-ref", f"refs/heads/{LANDING}", moved[0])
        return real(where, *args)

    monkeypatch.setattr(drain.gatelib, "git", racing)
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert (
        f"not landed: `{LANDING}` holds a commit this split did not make, so it stays as it is:"
        f" take what is yours from it, delete it, then run `drain.py split {ALPHA} --plan {plan}`"
        " again"
    ) in capsys.readouterr().out.splitlines()
    assert git(root, "rev-parse", LANDING) == moved[0], "the commit that moved it is kept"
    assert git(root, "status", "--porcelain") == "", "no part is filed a second time"


def test_a_rerun_keeps_a_landing_branch_whose_tip_is_not_the_commit_the_split_made(
    tmp_path: Path, capsys: Out
) -> None:
    root, plan, printed = red_split(tmp_path, capsys)
    children = json.loads(printed.splitlines()[-1])["children"]
    texts = {child: git(root, "show", f"{LANDING}:{child}") + "\n" for child in children}
    git(root, "branch", "-q", "-D", LANDING)
    git(root, "switch", "-q", "-c", LANDING)
    for child, text in texts.items():
        write(root, child, text)
    git(root, "add", "--", *children)
    git(root, "commit", "-q", "-m", "the owner's own commit of the parts")
    theirs = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    green_check(root)
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert f"not landed: `{LANDING}` holds a commit this split did not make" in (
        capsys.readouterr().out
    )
    assert git(root, "rev-parse", LANDING) == theirs


@pytest.mark.parametrize("killed_in", ["file_locally", "close"])
def test_a_rerun_after_a_kill_and_the_owner_taking_a_parts_number_lands_in_one_run(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, killed_in: str
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    module = drain.split if killed_in == "file_locally" else drain
    with monkeypatch.context() as cut:
        cut.setattr(module, killed_in, killed_after(getattr(module, killed_in)))
        with pytest.raises(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    write(root, OTHER, ticket(4, "Other"))
    git(root, "add", "--", OTHER)
    git(root, "commit", "-q", "-m", "the owner files 04")
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert children == [f"{ISSUES}/05-save.md", f"{ISSUES}/06-list.md"], "past the owner's 04"
    assert git(root, "status", "--porcelain") == "", "the checkout is fit"
    on_main = git(root, "ls-tree", "--name-only", "main", "--", f"{ISSUES}/").splitlines()
    assert on_main == [ALPHA, BETA, GAMMA, OTHER, *children], "each part once"


def test_a_split_leaves_a_branch_named_as_its_landing_that_it_did_not_make(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "branch", LANDING)
    theirs = git(root, "rev-parse", LANDING)
    plan = str(plan_file(tmp_path, HALVES))
    for _ in range(2):
        assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
        assert (
            f"not landed: `{LANDING}` exists, and the split's note does not name it as the split's"
            " own, so it stays as it is: rename it; once the cause above is gone, run"
            f" `drain.py split {ALPHA} --plan {plan}` again"
        ) in capsys.readouterr().out.splitlines()
        assert git(root, "rev-parse", LANDING) == theirs


def test_a_note_naming_another_landing_branch_is_refused_and_that_branch_is_kept(
    tmp_path: Path, capsys: Out
) -> None:
    root, plan, _ = red_split(tmp_path, capsys)
    git(root, "branch", "release-1", "main")
    tip = git(root, "rev-parse", "release-1")
    path = queue_notes.note_path(root, BRANCH_A)
    queue_notes.write_note(path, {**note(root, BRANCH_A), "landing": "release-1"})
    green_check(root)
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert (
        f"not landed: the split's note {path} names `release-1` as its landing branch, and this"
        f" split lands on `{LANDING}`, so no branch is touched: correct the note, then run"
        f" `drain.py split {ALPHA} --plan {plan}` again"
    ) in capsys.readouterr().out.splitlines()
    assert git(root, "rev-parse", "release-1") == tip
    assert git(root, "branch", "--list", LANDING) != ""


def test_a_split_killed_between_its_parts_and_a_part_edited_lands_each_part_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    real = Path.write_text

    def once(path: Path, *args: object, **kwargs: object) -> int:
        done = real(path, *args, **kwargs)  # type: ignore[arg-type]
        if path.name == "04-save.md":
            raise Killed
        return done

    with monkeypatch.context() as cut:
        cut.setattr(Path, "write_text", once)
        with pytest.raises(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    first = root / ISSUES / "04-save.md"
    first.write_text(first.read_text(encoding="utf-8") + "\nThe owner's note.\n", encoding="utf-8")
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    landed_parts(root, [f"{ISSUES}/04-save.md", f"{ISSUES}/05-list.md"])
    assert "The owner's note." in git(root, "show", f"main:{ISSUES}/04-save.md")


def test_a_part_the_owner_staged_is_left_staged_and_the_split_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    with monkeypatch.context() as cut:
        cut.setattr(drain, "close", killed_after(drain.close))
        with pytest.raises(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    git(root, "add", "--", f"{ISSUES}/04-save.md")
    staged = git(root, "ls-files", "--stage", "--", f"{ISSUES}/04-save.md")
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    assert (
        f"not landed: the checkout's index holds {ISSUES}/04-save.md, so the parts stay"
        " uncommitted in the checkout: unstage them"
    ) in capsys.readouterr().out
    assert git(root, "ls-files", "--stage", "--", f"{ISSUES}/04-save.md") == staged
    assert git(root, "branch", "--list", "records/*") == ""
    git(root, "restore", "--staged", "--", f"{ISSUES}/04-save.md")
    assert run(root, "split", ALPHA, "--plan", plan) == 0


@pytest.mark.parametrize("what", ["conflict", "merge", "rebase"])
def test_a_split_mid_merge_or_rebase_writes_nothing_and_says_so(
    tmp_path: Path, capsys: Out, what: str
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "switch", "-q", "-c", "theirs")
    write(root, "src/a.py", "A = 2\n")
    commit(root, "theirs")
    git(root, "switch", "-q", "main")
    if what != "merge":
        write(root, "src/a.py", "A = 3\n")
        commit(root, "ours")
    with contextlib.suppress(subprocess.CalledProcessError):
        if what == "rebase":
            git(root, "switch", "-q", "theirs")
            git(root, "rebase", "-q", "main")
        else:
            git(root, "merge", "-q", "--no-commit", "--no-ff", "theirs")
    if what == "conflict":
        (root / ".git/MERGE_HEAD").unlink()
    before, refs = owners_view(root), git(root, "for-each-ref")
    noted = note(root, BRANCH_A)
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == 2
    assert capsys.readouterr().out.splitlines() == [
        (
            "split: the checkout is in the middle of a merge or a rebase, or its index holds a"
            f" conflict, so nothing is filed: finish it, then run `drain.py split {ALPHA} --plan"
            f" {plan}` again"
        )
    ]
    assert (owners_view(root), git(root, "for-each-ref"), note(root, BRANCH_A)) == (
        before,
        refs,
        noted,
    )
    assert not list((root / ISSUES).glob("0[4-9]*"))


def test_a_landing_branch_git_cannot_make_leaves_the_parts_in_the_checkout_and_says_why(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "branch", f"{LANDING}/mine")
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    assert any(line.startswith("records: the parts were not committed: ") for line in printed)
    children = json.loads(printed[-1])["children"]
    assert git(root, "status", "--porcelain").splitlines() == [f"?? {c}" for c in children]
    assert git(root, "branch", "--list", "records/*").split() == [f"{LANDING}/mine"]


def test_parts_outside_the_sparse_checkout_stay_in_the_checkout_and_the_split_says_why(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    git(root, "sparse-checkout", "set", "--no-cone", "/*", f"!/{ISSUES}/*-save.md")
    plan = str(plan_file(tmp_path, HALVES))
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", plan) == drain.NOT_LANDED
    printed = capsys.readouterr().out.splitlines()
    said = next(line for line in printed if line.startswith("records: the parts were not"))
    assert f"{ISSUES}/04-save.md" in said
    assert git(root, "branch", "--list", "records/*") == ""
    children = json.loads(printed[-1])["children"]
    assert all((root / child).is_file() for child in children), "the parts stay in the checkout"


def test_a_rerun_after_the_parts_landed_keeps_them_though_the_owner_took_a_parts_number(
    tmp_path: Path,
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    plan = str(plan_file(tmp_path, HALVES))
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    write(root, OTHER, ticket(4, "Other"))
    tip = commit(root, "the owner files 04")
    assert run(root, "split", ALPHA, "--plan", plan) == 0
    assert git(root, "status", "--porcelain") == "", "no landed part is moved"
    assert git(root, "rev-parse", "main") == tip


EDITS = st.lists(st.sampled_from(["parts", "mode", "rename", "delete", "extra"]), min_size=1)
OWNERS = st.lists(
    st.sampled_from(["staged", "unstaged", "untracked", "record", "rename"]), unique=True
)


def owners_edits(root: Path, kinds: list[str]) -> None:
    for kind in kinds:
        if kind == "staged":
            write(root, "src/b.py", "B = 1\n")
            git(root, "add", "--", "src/b.py")
        elif kind == "unstaged":
            write(root, "src/a.py", "A = 3\n")
        elif kind == "untracked":
            write(root, "docs/draft.md", "a draft\n")
        elif kind == "record":
            write(root, f"{ISSUES}/09-draft.md", "# 09: half a thought\n")
            git(root, "add", "--", f"{ISSUES}/09-draft.md")
        else:
            git(root, "mv", GAMMA, f"{ISSUES}/03-gamma-mine.md")


def someone_else(root: Path, n: int, edits: list[str], *, on: str, texts: dict[str, str]) -> None:
    tree = root.parent / f"someone-{n}"
    git(
        root,
        "worktree",
        "add",
        "-q",
        *([str(tree), on] if on else ["-b", f"records/s{n}", str(tree), "main"]),
    )
    for edit in edits:
        if edit == "parts":
            for child, text in texts.items():
                write(tree, child, text)
            git(tree, "add", "--", *texts)
        elif edit == "mode" and (tree / GAMMA).exists():
            (tree / GAMMA).chmod((tree / GAMMA).stat().st_mode ^ 0o111)
            git(tree, "add", "--", GAMMA)
        elif edit == "rename" and (tree / GAMMA).exists():
            git(tree, "mv", GAMMA, f"{ISSUES}/03-gamma-{n}.md")
        elif edit == "delete" and (tree / BETA).exists():
            git(tree, "rm", "-q", BETA)
        else:
            write(tree, f"docs/notes-{n}.md", f"{edit} {n}\n")
            git(tree, "add", "--", f"docs/notes-{n}.md")
    git(tree, "commit", "-q", "--allow-empty", "-m", f"someone else's {n}")
    git(root, "worktree", "remove", str(tree))


@functools.cache
def whole() -> Path:
    built = project(Path(tempfile.mkdtemp(prefix="ratchet-whole-")), **WHOLE)
    atexit.register(shutil.rmtree, built.parent, ignore_errors=True)
    return built


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    killed=st.booleans(),
    owners=OWNERS,
    strangers=st.lists(st.tuples(st.booleans(), EDITS), min_size=1, max_size=3),
    greens=st.lists(st.booleans(), min_size=1, max_size=3),
)
def test_no_split_run_loses_a_commit_or_moves_the_owners_work(
    tmp_path: Path,
    *,
    killed: bool,
    owners: list[str],
    strangers: list[tuple[bool, list[str]]],
    greens: list[bool],
) -> None:
    root = Path(tempfile.mkdtemp(dir=tmp_path)) / "repo"
    shutil.copytree(whole(), root, symlinks=True)
    run(root, "open", ALPHA)
    plan = str(plan_file(root.parent, HALVES))
    if not killed:
        green_check(root, green=False)
    owners_edits(root, owners)
    work = owners_work(root)
    filed = set((root / ISSUES).iterdir())
    with pytest.MonkeyPatch.context() as cut:
        if killed:
            cut.setattr(drain.split, "file_locally", killed_after(drain.split.file_locally))
        with contextlib.suppress(Killed):
            run(root, "split", ALPHA, "--plan", plan)
    assert owners_work(root) == work, "the split leaves the owner's work as it was"
    own = git(root, "branch", "--list", "--format=%(refname:short)", "records/*")
    if killed:
        texts = {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(set((root / ISSUES).iterdir()) - filed)
        }
    else:
        made = git(root, "diff", "--name-only", f"main...{own}").splitlines()
        texts = {child: git(root, "show", f"{own}:{child}") + "\n" for child in made}
    touched = False
    for n, (on_own, edits) in enumerate(strangers):
        mine = bool(on_own and own)
        touched |= mine
        someone_else(root, n, edits, on=own if mine else "", texts=texts)
    kept = f"--exclude={own}" if own and not touched else "--exclude=none"
    before = set(git(root, "rev-list", kept, "--branches").splitlines())
    for green in greens:
        green_check(root, green=green)
        run(root, "split", ALPHA, "--plan", plan)
        assert owners_work(root) == work, "a rerun leaves the owner's work as it was"
    after = set(git(root, "rev-list", "--branches").splitlines())
    assert before <= after, f"{len(before - after)} commit(s) no branch holds any more"
    assert all(names == list(texts) for names in split_commits(root)), "each holds the parts"


OWNED = {
    "src/staged.py": "S = 1\n",
    "src/unstaged.py": "U = 1\n",
    "src/old.py": "O = 1\n",
    "src/deleted.py": "D = 1\n",
    "src/mode.sh": "echo m\n",
    "src/partial.py": "P = 1\n",
    "src/assumed.py": "A = 1\n",
    "src/skipped.py": "K = 1\n",
    "extra/out.txt": "outside the cone\n",
}
MIXED = (
    "staged",
    "unstaged",
    "untracked",
    "renamed",
    "deleted",
    "mode",
    "intent",
    "partial",
    "assumed",
    "gitlink",
)
OWNER_STATES = {
    "sparse and mixed": ("sparse", *MIXED),
    "skip-worktree and mixed": ("skipped", *MIXED),
    "none": (),
}
FULL_GRID = os.environ.get("RATCHET_SPLIT_GRID") == "full"
GRID_STATES = list(OWNER_STATES) if FULL_GRID else ["sparse and mixed"]
KILL_POINTS = 29
GRID_KILLS = range(KILL_POINTS) if FULL_GRID else range(0, KILL_POINTS, 4)
PART_PATH = re.compile(rf"{re.escape(ISSUES)}/\d+-(save|list)\.md")
HOOKED = "docs/hooked.md"
KEPT_FLAGS = 0xF000 | 1 << 29 | 1 << 30
UNSEEN = {".git/objects", ".git/logs"}


def owners_state(root: Path, kinds: Sequence[str]) -> None:
    steps: dict[str, list[tuple[str, ...]]] = {
        "sparse": [("sparse-checkout", "set", "--cone", ".scratch", "docs", "guards", "src")],
        "staged": [("add", "--", "src/staged.py")],
        "renamed": [("mv", "src/old.py", "src/new.py")],
        "deleted": [("rm", "-q", "--", "src/deleted.py")],
        "mode": [("update-index", "--chmod=+x", "--", "src/mode.sh")],
        "intent": [("add", "-N", "--", "src/intent.py")],
        "partial": [("add", "--", "src/partial.py")],
        "assumed": [("update-index", "--assume-unchanged", "--", "src/assumed.py")],
        "skipped": [("update-index", "--skip-worktree", "--", "src/skipped.py")],
        "gitlink": [("update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},src/vendor/sub")],
    }
    before = {"staged": "S = 2\n", "intent": "I = 1\n", "partial": "P = 2\n"}
    after = {
        "unstaged": "U = 2\n",
        "untracked": "N = 1\n",
        "partial": "P = 3\n",
        "assumed": "A = 2\n",
        "skipped": "K = 2\n",
    }
    for kind in kinds:
        if kind in before:
            write(root, f"src/{kind}.py", before[kind])
        for step in steps.get(kind, []):
            git(root, *step)
        if kind in after:
            write(root, f"src/{kind}.py", after[kind])


def theirs(line: str) -> bool:
    return not PART_PATH.search(line) and HOOKED not in line


def raw(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", "-C", str(root), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def index_view(root: Path) -> list[str]:
    flags = [
        int(line.rsplit(" ", 1)[1], 16) & KEPT_FLAGS
        for line in git(root, "ls-files", "--debug").splitlines()
        if "flags:" in line
    ]
    rows = git(root, "ls-files", "--stage").splitlines()
    return [f"{row} {flag:x}" for row, flag in zip(rows, flags, strict=True) if theirs(row)]


def files_view(root: Path) -> dict[str, tuple[str, int]]:
    seen = {}
    for top, dirs, names in os.walk(root):
        here = Path(top).relative_to(root).as_posix()
        dirs[:] = [d for d in dirs if here != "." or d not in {".git", str(drain.gatelib.STATE)}]
        for name in names:
            path = Path(top) / name
            rel = path.relative_to(root).as_posix()
            if theirs(rel):
                shown = path.lstat()
                body = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
                seen[rel] = (hashlib.sha256(body).hexdigest(), shown.st_mode)
    return seen


def owners_view(root: Path) -> dict[str, object]:
    status = raw(root, "status", "--porcelain=v2", "--untracked-files=all").splitlines()
    refs = raw(root, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    split_own = ("refs/heads/main ", f"refs/heads/{LANDING}")
    return {
        "index": index_view(root),
        "cached": [r for r in raw(root, "diff", "--cached", "--raw").splitlines() if theirs(r)],
        "status": [line for line in status if theirs(line)],
        "files": files_view(root),
        "refs": [r for r in refs if not r.startswith(split_own)],
        "head": raw(root, "symbolic-ref", "-q", "HEAD"),
        "sparse": raw(root, "sparse-checkout", "list")
        + raw(root, "config", "--get-regexp", "^core\\.sparse"),
    }


def changed(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, was in before.items():
        now = after[name]
        if was == now:
            continue
        if isinstance(was, dict) and isinstance(now, dict):
            keys = {k for k in {*was, *now} if was.get(k) != now.get(k)}
            out[name] = {k: (was.get(k), now.get(k)) for k in keys}
        elif isinstance(was, list) and isinstance(now, list):
            out[name] = {"gone": sorted(set(was) - set(now)), "new": sorted(set(now) - set(was))}
        else:
            out[name] = (was, now)
    return out


def fingerprint(root: Path) -> frozenset[tuple[str, int, int, int]]:
    seen = set()
    for top, dirs, names in os.walk(root):
        here = Path(top).relative_to(root).as_posix()
        dirs[:] = [d for d in dirs if f"{here}/{d}" not in {f"./{u}" for u in UNSEEN}]
        for name in names:
            shown = (Path(top) / name).lstat()
            seen.add((f"{here}/{name}", shown.st_size, shown.st_mtime_ns, shown.st_mode))
    return frozenset(seen)


class Cut:
    def __init__(self, root: Path, at: int | None = None) -> None:
        self.root, self.at, self.count, self.points, self.dead = root, at, 0, [], False
        self.last = fingerprint(root) if at is None else frozenset()

    def event(self) -> None:
        if self.dead:
            raise Killed
        if self.at is None:
            now = fingerprint(self.root)
            if now != self.last:
                self.points.append(self.count)
            self.last = now
        elif self.count == self.at:
            self.dead = True
            raise Killed
        self.count += 1

    def install(self, cut: pytest.MonkeyPatch) -> None:
        real_run = subprocess.run

        def running(args: object, *rest: object, **kwargs: object) -> object:
            if not (isinstance(args, list) and args and args[0] == "git"):
                return real_run(args, *rest, **kwargs)  # type: ignore[call-overload]
            self.event()
            done = real_run(args, *rest, **kwargs)  # type: ignore[call-overload]
            self.event()
            return done

        cut.setattr(subprocess, "run", running)
        for name in ("write_text", "unlink", "replace"):
            real = getattr(Path, name)

            def writing(
                path: Path, *rest: object, _real: object = real, **kwargs: object
            ) -> object:
                done = _real(path, *rest, **kwargs)  # type: ignore[operator]
                self.event()
                return done

            cut.setattr(Path, name, writing)


@functools.cache
def owners_project() -> Path:
    built = Path(tempfile.mkdtemp(prefix="ratchet-owner-"))
    atexit.register(shutil.rmtree, built, ignore_errors=True)
    return project(built, **WHOLE, **OWNED)


def owners_copy(tmp_path: Path, state: str) -> Path:
    root = Path(tempfile.mkdtemp(dir=tmp_path)) / "repo"
    shutil.copytree(owners_project(), root, symlinks=True)
    run(root, "open", ALPHA)
    owners_state(root, OWNER_STATES[state])
    return root


HOOKS = ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit")
HOOK_ACTIONS = {
    "refuses": "exit 1",
    "stages": f"printf x > {HOOKED} && git add -- {HOOKED}",
    "writes": f"printf x > {HOOKED}",
    "edits a part": f"for f in {ISSUES}/*-save.md; do printf 'edited\\n' >> \"$f\"; done",
}
COMMITS = (
    '[ -n "$HOOKED" ] && exit 0\n'
    f"printf y > {HOOKED} && git add -- {HOOKED} && HOOKED=1 git commit -q -m hook-made"
    ' && git rev-parse HEAD >> "$HOOK_COMMITS"'
)
EXITS = [
    "landed",
    "judge",
    "red",
    *(f"{name} {action}" for name in HOOKS for action in HOOK_ACTIONS),
    "post-commit commits",
]
GRID_EXITS = (
    EXITS
    if FULL_GRID
    else [
        "landed",
        "judge",
        "red",
        "pre-commit refuses",
        "commit-msg stages",
        "prepare-commit-msg writes",
        "post-commit edits a part",
        "post-commit commits",
    ]
)


def drive(root: Path, exit_: str, cut: pytest.MonkeyPatch) -> list[Path]:
    verdict = [merge_ready.Reason("record", "refused")] if exit_ == "judge" else []
    cut.setattr(drain.merge.merge_ready, "judge", lambda *_a, **_k: verdict)
    if exit_ == "red":
        cut.setattr(drain.merge, "run_check", lambda *_a, **_k: merge_ready.Reason("gates", "red"))
    elif exit_ not in {"landed", "judge"}:
        name, _, action = exit_.partition(" ")
        body = COMMITS if action == "commits" else HOOK_ACTIONS[action]
        return [hook_script(root / ".git/hooks", name, body)]
    return []


def where_the_parts_are(root: Path, code: int | str, printed: str) -> list[str]:
    children = note(root, BRANCH_A).get("children")
    if not isinstance(children, list):
        return []
    lines = printed.splitlines()
    said = next((line for line in lines if line.startswith("not landed:")), "")
    named = re.search(r"the parts are on `([^`]+)`", said)
    if code == 0:
        held = git(root, "ls-tree", "--name-only", "main", "--", *children).splitlines()
        return [] if held == children else [f"exit 0, and main holds {held}"]
    if named:
        held = raw(root, "ls-tree", "--name-only", named[1], "--", *children).splitlines()
        return [] if held == children else [f"{said!r}, and `{named[1]}` holds {held}"]
    if "uncommitted in the checkout" in said:
        gone = [child for child in children if not (root / child).is_file()]
        return [f"{said!r}, and the checkout lacks {gone}"] if gone else []
    return []


def landed_state(root: Path) -> list[str]:
    children = note(root, BRANCH_A).get("children")
    assert isinstance(children, list)
    problems = [
        f"{child} is not on main, committed once"
        for child in children
        if len(raw(root, "rev-list", "main", "--", child).split()) != 1
    ]
    filed = sorted(p.relative_to(root).as_posix() for p in (root / ISSUES).glob("*.md"))
    if filed != sorted([ALPHA, BETA, GAMMA, *children]):
        problems.append(f"the tracker holds {filed}, not each part once")
    if raw(root, "status", "--porcelain", "--", *children):
        problems.append("the checkout does not hold the parts as committed")
    if raw(root, "branch", "--list", "records/*"):
        problems.append(f"a records branch is left: {raw(root, 'branch', '--list', 'records/*')}")
    return problems


def owners_kept(root: Path, before: dict[str, object], *, tip: str, made: set[str]) -> list[str]:
    problems = []
    moved = changed(before, owners_view(root))
    if moved:
        problems.append(f"the owner's state changed: {moved}")
    now = git(root, "rev-parse", "main")
    names = git(root, "diff", "--name-only", tip, now).splitlines()
    first = raw(root, "rev-parse", "main^1").strip()
    if now != tip and (first != tip or not all(map(PART_PATH.fullmatch, names))):
        problems.append(f"main gained more than the parts: {names}")
    reachable = set(raw(root, "rev-list", "--all").split())
    lost = made - reachable
    if lost:
        problems.append(f"{len(lost)} commit(s) no ref reaches any more")
    return problems


def one_exit(
    tmp_path: Path, capsys: Out, *, state: str, exit_: str, at: int | None = None
) -> list[str]:
    root = owners_copy(tmp_path, state)
    plan = str(plan_file(root.parent, HALVES))
    made = tmp_path / "hook-commits"
    before, tip = owners_view(root), git(root, "rev-parse", "main")
    commits = set(git(root, "rev-list", "--all").split())
    capsys.readouterr()
    with pytest.MonkeyPatch.context() as cut:
        cut.setenv("HOOK_COMMITS", str(made))
        hooks = drive(root, exit_, cut)
        if at is not None:
            Cut(root, at).install(cut)
        try:
            code: int | str = run(root, "split", ALPHA, "--plan", plan)
        except Killed:
            code = "killed"
    for hook in hooks:
        hook.unlink()
    printed = capsys.readouterr().out
    hooked = set(made.read_text(encoding="utf-8").split()) if made.is_file() else set()
    problems = [
        f"first run: {p}"
        for p in [
            *owners_kept(root, before, tip=tip, made=commits | hooked),
            *where_the_parts_are(root, code, printed),
        ]
    ]
    if at is not None:
        for part in (root / ISSUES).glob("*.md"):
            name = part.relative_to(root).as_posix()
            if PART_PATH.fullmatch(name) and not raw(root, "ls-files", "--", name):
                part.write_text(part.read_text(encoding="utf-8") + "The owner's edit.\n")
    kept = hooks_made(root)
    again = run(root, "split", ALPHA, "--plan", plan)
    printed = capsys.readouterr().out
    problems += [
        f"rerun: {p}"
        for p in [
            *owners_kept(root, before, tip=tip, made=commits | hooked),
            *where_the_parts_are(root, again, printed),
        ]
    ]
    said = [line for line in printed.splitlines() if line.startswith(("not landed", "records:"))]
    if kept:
        if again == 0 or raw(root, "rev-parse", LANDING).strip() != kept:
            problems.append(f"the rerun let go of what the hooks made on `{LANDING}`: {said}")
    elif again != 0:
        problems.append(f"the rerun exits {again}: {said}")
    else:
        problems += [f"rerun: {p}" for p in landed_state(root)]
    return problems


def hooks_made(root: Path) -> str:
    held = raw(root, "rev-parse", "--verify", "--quiet", LANDING).strip()
    if not held:
        return ""
    names = raw(root, "diff", "--name-only", f"main...{held}").split()
    alone = raw(root, "rev-list", "--count", f"main..{held}").strip() in {"0", "1"}
    return "" if alone and all(map(PART_PATH.fullmatch, names)) else held


@functools.cache
def kill_points(state: str) -> tuple[int, ...]:
    with tempfile.TemporaryDirectory() as made:
        root = owners_copy(Path(made), state)
        recorder = Cut(root)
        with pytest.MonkeyPatch.context() as cut:
            drive(root, "landed", cut)
            recorder.install(cut)
            run(root, "split", ALPHA, "--plan", str(plan_file(root.parent, HALVES)))
    return tuple(recorder.points)


@pytest.mark.parametrize(
    ("state", "exit_"),
    [
        pytest.param(state, exit_, marks=pytest.mark.shard_slot(n))
        for n, (state, exit_) in enumerate((s, e) for s in GRID_STATES for e in GRID_EXITS)
    ],
)
def test_every_exit_of_a_split_keeps_the_owners_work_and_a_rerun_lands_the_parts(
    tmp_path: Path, capsys: Out, *, state: str, exit_: str
) -> None:
    problems = one_exit(tmp_path, capsys, state=state, exit_=exit_)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "state", [pytest.param(state, marks=pytest.mark.shard_slot(10)) for state in GRID_STATES]
)
def test_the_harness_kills_the_split_between_every_two_of_its_writes(
    state: str, capsys: Out
) -> None:
    assert len(kill_points(state)) >= KILL_POINTS, "fewer writes than kill slots: lower KILL_POINTS"
    capsys.readouterr()


@pytest.mark.parametrize(
    ("state", "kill"),
    [
        pytest.param(state, kill, marks=pytest.mark.shard_slot(10 + n // 4))
        for n, (state, kill) in enumerate((s, k) for s in GRID_STATES for k in GRID_KILLS)
    ],
)
def test_a_split_killed_between_any_two_writes_keeps_the_owners_work_and_a_rerun_lands_it(
    tmp_path: Path, capsys: Out, *, state: str, kill: int
) -> None:
    problems = []
    for at in kill_points(state)[kill::KILL_POINTS]:
        (tmp_path / str(at)).mkdir()
        found = one_exit(tmp_path / str(at), capsys, state=state, exit_="landed", at=at)
        problems += [f"killed at write event {at}: {p}" for p in found]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "parts",
    [
        [HALVES[0], {"title": "More", "promises": ["It mines coins."]}],
        [HALVES[0], {"title": "List"}],
        [HALVES[0], {"title": "List", "promises": ["It lists the links.", "It saves a link."]}],
    ],
    ids=["an added promise", "a dropped one", "a doubled one"],
)
def test_a_refused_split_parks_the_ticket_as_too_large(
    tmp_path: Path, capsys: Out, parts: list[dict[str, object]]
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, parts))) == 1
    assert not list((root / ".scratch/f/issues").glob("0[4-9]*"))
    reasons = note(root, BRANCH_A)["reasons"]
    assert any("split was refused" in reason for reason in reasons)  # type: ignore[attr-defined]


def test_a_part_of_a_split_is_not_split_again(tmp_path: Path, capsys: Out) -> None:
    child = ticket(4, "Child", f"Split from: {ALPHA}\n\n") + PROMISES
    root = project(tmp_path, **WHOLE, **{CHILD: child})
    run(root, "open", CHILD)
    capsys.readouterr()
    assert run(root, "split", CHILD, "--plan", str(plan_file(tmp_path, HALVES))) == 1


def test_the_parent_is_read_from_the_default_branch_not_the_work_tree(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    write(drain.tree_of(root, BRANCH_A), ALPHA, WHOLE[ALPHA] + "- [ ] It mines coins.\n")
    capsys.readouterr()
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0


def test_a_split_parent_is_not_built_and_a_ticket_waiting_on_it_waits_for_its_parts(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    assert states(root)[BRANCH_A] != "buildable"
    waiting = {entry.branch: entry for entry in drain.plan(offline(root)).tickets}[BRANCH_B]
    assert waiting.state == "waiting"
    assert waiting.waits_on == tuple(drain.key(child) for child in children)
    for child in children:
        text = (root / child).read_text(encoding="utf-8")
        (root / child).write_text(text.replace("Status:", "Status: done\nWas:"), encoding="utf-8")
    commit(root, "the parts are done")
    waiting = {entry.branch: entry for entry in drain.plan(offline(root)).tickets}[BRANCH_B]
    assert waiting.state == "buildable"


def test_a_split_whose_note_names_no_parts_holds_its_waiting_ticket(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    capsys.readouterr()
    path = queue_notes.note_path(root, BRANCH_A)
    queue_notes.write_note(path, {**queue_notes.read_note(root, BRANCH_A), "children": []})
    gh = offline(root).tracker
    waiting = drain.preflight.fetch(BETA, root, gh)
    assert waiting is not None
    assert drain.preflight.open_blockers(waiting, root, gh) == [
        f"blocker {ALPHA} was split into no parts"
    ]


def test_a_plan_that_does_not_parse_is_a_refused_split_and_a_split_parent_blocks_by_its_parts(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **WHOLE)
    run(root, "open", ALPHA)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert run(root, "split", ALPHA, "--plan", str(bad)) == 1, "an unreadable plan splits nothing"
    run(root, "open", ALPHA)
    assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
    children = json.loads(capsys.readouterr().out.splitlines()[-1])["children"]
    gh = offline(root).tracker
    waiting = drain.preflight.fetch(BETA, root, gh)
    assert waiting is not None
    assert "was split, and its part" in " ".join(drain.preflight.open_blockers(waiting, root, gh))
    split_parent = drain.preflight.fetch(ALPHA, root, gh)
    assert split_parent is not None
    report = drain.preflight.Report(ref=ALPHA)
    drain.preflight.check_ticket(report, split_parent, "ready-for-agent", root=root, gh=gh)
    assert "split" in [failure.check for failure in report.failures]
    for child in children:
        text = (root / child).read_text(encoding="utf-8")
        (root / child).write_text(text.replace("Status:", "Status: done\nWas:"), encoding="utf-8")
    assert drain.preflight.open_blockers(waiting, root, gh) == []


GATED_BINDING = binding(MERGE) + (
    "\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
    "| --- | --- | --- | --- | --- |\n| the browser suite | driver | 2 | | `just e2e-browser` |\n"
)


def test_open_hands_the_pipeline_the_gates_the_tickets_criteria_name(
    tmp_path: Path, capsys: Out
) -> None:
    criteria = "## Acceptance criteria\n\n- [ ] `e2e-browser` is green\n- [ ] the list shows\n\n"
    root = project(
        tmp_path,
        **{"docs/process/binding.md": GATED_BINDING, ALPHA: ticket(1, "Alpha", criteria)},
    )
    assert run(root, "open", ALPHA) == 0
    assert json.loads(capsys.readouterr().out)["named_gates"] == [
        {"gate": "e2e-browser", "tier": "2", "criterion": "`e2e-browser` is green"}
    ]


def test_open_hands_the_pipeline_no_gate_when_no_criterion_names_one(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, **{"docs/process/binding.md": GATED_BINDING})
    assert run(root, "open", ALPHA) == 0
    assert json.loads(capsys.readouterr().out)["named_gates"] == []
