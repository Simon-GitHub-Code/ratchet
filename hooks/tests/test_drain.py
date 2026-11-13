"""``drain.py``: the queue's plan, its work trees, its merges and its one report.

Every test runs on a real repository with local tickets, since the plan is read from git, the
ticket files and the notes, and a work tree is only proved gone by asking git.
"""

import json
import os
import signal
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import drain
import hooks_installed
import merge_ready
import queue_notes
from tests import servers
from tests.repos import commit, git, repository, write
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
    """The hooks are installed, the temp directory is the test's own, and the clock stands still."""
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
    """Commit ``files`` in the ticket's work tree as the pipeline's orchestrator would."""
    tree = drain.tree_of(root, branch)
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


# --- the plan --------------------------------------------------------------------------------


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
    """Answers ``issue list`` and ``issue view`` from a table."""

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


# --- opening and the states a work tree gives ------------------------------------------------


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


# --- update ----------------------------------------------------------------------------------


def moved(root: Path, path: str = "src/m.py", text: str = "M = 1\n") -> None:
    """Another change lands on the default branch."""
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
    commit(tree, "feat(core): the change")
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


def test_update_refuses_to_move_a_judged_or_a_pushed_branch(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"}, record=approval("alpha"))
    moved(root)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 1
    assert "its last commit is a verdict record" in capsys.readouterr().out
    git(tree, "reset", "-q", "--hard", "HEAD~1")
    git(root, "update-ref", "refs/remotes/origin/feature/1-alpha", BRANCH_A)
    assert run(root, "update", ALPHA) == 1
    assert "the branch is pushed" in capsys.readouterr().out


# --- landing and closing ---------------------------------------------------------------------


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
    assert states(root)[BRANCH_A] == "built", (
        "a later drain asks the judge again, and builds nothing"
    )


def test_land_needs_the_merge_entry_point_and_no_forge(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, **{"docs/process/binding.md": binding(None)})
    assert run(root, "land", ALPHA) == 2
    assert capsys.readouterr().out.startswith("land: with a forge")
    write(root, "docs/process/binding.md", binding("/no/such/command"))
    commit(root, "docs(process): a merge that cannot run")
    assert run(root, "land", ALPHA) == 2
    assert "could not run" in capsys.readouterr().out
    write(root, "docs/process/binding.md", binding(f"{sys.executable} -c 'raise SystemExit(5)'"))
    commit(root, "docs(process): a merge that fails")
    assert run(root, "land", ALPHA) == 2
    assert note(root, BRANCH_A) == {}, "nothing is closed on an answer that is no verdict"
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


def test_close_parks_a_ticket_with_the_results_reasons_and_stashes_its_draft(
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
    assert capsys.readouterr().out == f"closed: {GAMMA}: parked\n"
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
    assert git(root, "stash", "list", "--format=%gs") == f"On {BRANCH_C}: {BRANCH_C}: blocked run"
    assert states(root)[BRANCH_C] == "held"
    held = next(e for e in drain.plan(offline(root)).tickets if e.branch == BRANCH_C)
    assert held.why[0] == "first"
    assert run(root, "open", GAMMA) == 1
    assert "it is held; first" in capsys.readouterr().out
    write(root, GAMMA, ticket(3, "Gamma") + "\n## Answers\n\nA page that says it expired.\n")
    commit(root, "docs(tracker): the owner's answer")
    assert states(root)[BRANCH_C] == "buildable", "the ticket changed, so the hold is over"


def test_a_number_names_the_one_local_ticket_it_starts(tmp_path: Path, capsys: Out) -> None:
    # Why: issue 26. With no tracker `03`, `#3` and `3` all mean the file `03-...`.
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
    assert run(root, "close", ALPHA, "--result", str(tmp_path / "missing.json")) == 0
    kept = note(root, BRANCH_A)
    assert (kept["outcome"], kept["reasons"], kept["assumptions"]) == ("waits", [], [])
    assert (kept["additions"], kept["gates"]) == ([], {}), "a branch that was never made holds none"


# --- clean -----------------------------------------------------------------------------------


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
        f"removed: {tree_a}: its branch is merged",
        f"kept: {tree_c}: `{BRANCH_C}` is not merged",
        f"removed: {stray}: no work tree is registered there",
    ]
    assert tree_c.is_dir()
    assert not tree_a.exists()
    git(root, "update-ref", "-d", f"refs/heads/{BRANCH_C}")
    assert run(root, "clean") == 0
    assert capsys.readouterr().out == f"removed: {tree_c}: its branch is gone\n"
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
        f"removed: {tree_a}: the drain that made it was interrupted",
        f"removed: {tree_c}: the drain that made it was interrupted",
    ]
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(root, "stash", "list", "--format=%gs") == (
        f"On {BRANCH_A}: {BRANCH_A}: interrupted run"
    )
    assert states(root) == {BRANCH_A: "buildable", BRANCH_C: "buildable", BRANCH_B: "waiting"}
    assert run(root, "clean", "--all") == 0, "a second clean finds nothing and says nothing"
    assert capsys.readouterr().out == ""


# --- the report ------------------------------------------------------------------------------


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
        "- `.scratch/f/issues/04-delta.md` Delta: refused",
        (
            "  - conjecture: .scratch/f/issues/04-delta.md has no `## Conjecture` section and names"
            " no parent -> the owner adds `## Conjecture` to the ticket, or to its parent spec:"
            " `/ratchet:conjecture <ticket>` drafts it (the approach, the observation that would"
            " refute it, the alternatives and the criticism that kills each)"
        ),
        f"- `{BETA}` Beta: waiting",
        "  - waits on .scratch/f/issues/05-eps.md",
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
        f"- `{ALPHA}`: 42 min in the queue",
        f"- `{GAMMA}`: 42 min in the queue",
        "- `.scratch/f/issues/05-eps.md`: 42 min in the queue",
    ]
    assert text.endswith("\n")
    assert git(root, "status", "--porcelain") == "", "the report is no commit's"
    assert all(note(root, b)["reported"] is True for b in (BRANCH_A, BRANCH_C, "feature/5-eps"))
    assert run(root, "report") == 0
    again = capsys.readouterr().out
    assert "## Merged\n\n- none\n" in again, "a ticket is reported once"
    assert f"## Parked\n\n- `{GAMMA}` Gamma (`{BRANCH_C}`)\n  - open question: which?\n" in again
    assert "- `.scratch/f/issues/05-eps.md` Eps: built\n" in again


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


# --- the small readers -----------------------------------------------------------------------


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
    """A directory of the owner's that the queue must never empty."""
    precious = tmp_path / "precious"
    write(precious, "kept/notes.txt", "the owner's\n")
    return precious


@pytest.mark.parametrize("level", ["user", "repository"])
def test_a_queue_directory_that_is_a_link_is_refused_and_what_it_points_at_is_kept(
    tmp_path: Path, capsys: Out, level: str
) -> None:
    # Why: the temp directory is shared and the name is predictable. A link planted there made
    # `clean` empty the directory it pointed at.
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
        assert run(root, "clean", "--all") == 0
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
OWED_RED = (
    "triggers: 'outbound-call' gained 1 instance(s) (src/b.py:1) while the base already held 1"
    " and these rows are still `later:`: survives failing dependencies. Run /ratchet:safeguard"
)


def test_a_ticket_red_on_an_owed_safeguard_is_held_until_the_safeguard_is_bound(
    tmp_path: Path, capsys: Out
) -> None:
    # Why: ADR 0028. A second outbound call while its safeguard is owed is red, and no rewrite of
    # the ticket fixes that; binding the safeguard on the default branch does.
    files = {
        "docs/process/binding.md": binding(MERGE) + OWED_BINDING,
        "src/b.py": "requests.get(1)\n",
    }
    root = project(tmp_path, **files)
    run(root, "open", GAMMA)
    result = tmp_path / "result.json"
    output = f"{OWED_RED}\n{OWED_RED}\ncheck: red"
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
