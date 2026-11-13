"""The route: the next command is decided from the checkout, stage by stage."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

import hooks_installed
import route
from tests.repos import branch, commit, git, install_hooks, repository, write

Out = pytest.CaptureFixture[str]
REAL_CHECK = hooks_installed.check
STANZA = "# P\n\nLoad the `ratchet:method` skill first.\n"
CONJECTURE = (
    "\n\n## Conjecture\n\nOne script decides the route; the refuter is a wrong command on a"
    " known state. Alternatives: the model guesses (killed: it drifts).\n"
)
BINDING = (
    "# Binding\n\n- Default branch: `main`\n\n| Entry point | Command |\n| --- | --- |\n"
    "| check | `just check` |\n"
    "| merge | `just merge` |\n"
)
ADOPTED = {
    "AGENTS.md": STANZA,
    "docs/agents/issue-tracker.md": "GitHub\n",
    "docs/process/binding.md": BINDING,
    "GLOSSARY.md": "# Glossary\n\n**Customer**: who pays.\n",
    "docs/design-brief.md": "# Brief\n",
}


@pytest.fixture(autouse=True)
def hooks_are_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stage below is asked with the commit hooks in place; the hooks stage has its tests."""
    monkeypatch.setattr(hooks_installed, "check", lambda _root: None)


class FakeGh:
    """Answers ``issue list`` and ``pr view`` from a table, remembering the calls."""

    def __init__(self, **answers: str | None) -> None:
        self.answers = answers
        self.calls: list[Sequence[str]] = []

    def __call__(self, args: Sequence[str]) -> str | None:
        self.calls.append(args)
        if args[0] == "pr":
            return self.answers.get("pr")
        if args[1] == "view":
            return self.answers.get(f"issue_{args[2]}")
        return self.answers.get("issues")


def issues(*items: tuple[int, str, str]) -> str:
    return json.dumps([{"number": n, "title": t, "body": b} for n, t, b in items])


def test_the_stages_before_adoption(tmp_path: Path) -> None:
    assert route.decide(tmp_path / "loose", None).command == route.INIT
    root = repository(tmp_path, {"README.md": "# A\n"})
    assert route.decide(root, None) == route.Step(
        route.INIT, "AGENTS.md does not name the method", route.TRACKER_SETUP, "init"
    )
    write(root, "CLAUDE.md", STANZA)
    commit(root, "chore: stanza")
    assert route.decide(root, None).command == route.TRACKER_SETUP
    write(root, "docs/agents/issue-tracker.md", "GitHub\n")
    commit(root, "chore: tracker")
    assert route.decide(root, None).command == route.ADOPT
    assert route.decide(root, None).then == route.GRILL


def test_a_dirty_tree_is_finished_first(tmp_path: Path) -> None:
    root = repository(tmp_path, ADOPTED)
    write(root, "src/x.py", "x = 1\n")
    step = route.decide(root, None)
    assert step.command == "run `just check`, then commit on branch `main`"
    assert step.stage == "change"
    write(root, "docs/process/binding.md", "# Binding without a table\n")
    assert route.check_command(root) == route.CHECK_FALLBACK


def test_a_working_branch_waits_for_its_pull_request(tmp_path: Path) -> None:
    root = repository(tmp_path, ADOPTED)
    git(root, "remote", "add", "origin", "https://example.invalid/r.git")
    branch(root, "feature/4-x")
    assert route.decide(root, None).command == route.PULL_REQUEST
    assert route.decide(root, FakeGh(pr=None)).stage == "pull-request"
    assert route.decide(root, FakeGh(pr="not json")).stage == "pull-request"
    assert route.decide(root, FakeGh(pr='{"state": "MERGED"}')).stage == "pull-request"
    waiting = route.decide(root, FakeGh(pr='{"state": "OPEN", "url": "https://x/pull/1"}'))
    assert waiting.command == route.WAIT
    assert "https://x/pull/1" in waiting.why
    write(root, "src/x.py", "x = 1\n")
    assert route.decide(root, None).then == route.PULL_REQUEST


def test_the_records_come_before_any_ticket(tmp_path: Path) -> None:
    root = repository(tmp_path, {**ADOPTED, "GLOSSARY.md": "# <project>\n"})
    assert route.decide(root, None) == route.Step(
        route.GRILL,
        "GLOSSARY.md still holds the `<project>` placeholder",
        f"{route.SPEC}, then {route.TICKETS}",
        "records",
    )
    (root / "GLOSSARY.md").unlink()
    commit(root, "chore: drop")
    assert route.decide(root, None).why == "GLOSSARY.md is missing"
    write(root, "GLOSSARY.md", "# G\n")
    (root / "docs/design-brief.md").unlink()
    commit(root, "chore: brief")
    assert route.decide(root, None).why == "docs/design-brief.md is missing"


def test_tickets_on_the_tracker_route_to_conjecture_then_feature(tmp_path: Path) -> None:
    root = repository(tmp_path, ADOPTED)
    assert route.decide(root, FakeGh(issues="[]")).command == route.SPEC
    gh = FakeGh(issues=issues((5, "Five", "Body."), (3, "Three", "Body.")))
    step = route.decide(root, gh)
    assert step.command == f"{route.CONJECTURE} 3"
    assert '"Three"' in step.why
    assert step.then == f"{route.FEATURE} 3"
    assert gh.calls[0][:4] == ["issue", "list", "--state", "open"]
    assert "ready-for-agent" in gh.calls[0]
    built = route.decide(
        root, FakeGh(issues=issues((3, "Three", "Body."), (5, "Five", "X." + CONJECTURE)))
    )
    assert built.command == f"{route.FEATURE} 5"
    assert f"{route.POTETO} 5" in built.why
    inherited = FakeGh(
        issues=issues((7, "Seven", "**Parent:** #12\n\nBody.")),
        issue_12=json.dumps(
            {
                "number": 12,
                "title": "Spec",
                "body": "S." + CONJECTURE,
                "labels": [],
                "state": "OPEN",
            }
        ),
    )
    assert route.decide(root, inherited).command == f"{route.FEATURE} 7"


def test_a_ticket_that_does_not_say_how_it_is_operated_routes_to_conjecture(
    tmp_path: Path,
) -> None:
    typed = BINDING + "\n- Life events: deployed\n\n## Gates\n\n- Application types: cli\n"
    root = repository(tmp_path, ADOPTED | {"docs/process/binding.md": typed})
    step = route.decide(root, FakeGh(issues=issues((5, "Five", "X." + CONJECTURE))))
    assert step.command == f"{route.CONJECTURE} 5"
    assert step.why == 'ticket 5 "Five" is agent-ready but does not say how it is operated'
    said = "X." + CONJECTURE + "\n## Operability\n\n- Flag: none: the command only reads\n"
    assert route.decide(root, FakeGh(issues=issues((5, "Five", said)))).command == (
        f"{route.FEATURE} 5"
    )


def test_tracker_answers_are_read_defensively(tmp_path: Path) -> None:
    root = repository(tmp_path, ADOPTED)
    assert route.tracker_tickets(root, FakeGh(issues=None)) is None
    assert route.tracker_tickets(root, FakeGh(issues="nope")) is None
    assert route.tracker_tickets(root, FakeGh(issues='{"a": 1}')) == []
    assert route.tracker_tickets(root, FakeGh(issues='[1, {"number": "x"}]')) == []


def test_local_tickets_when_there_is_no_tracker(tmp_path: Path) -> None:
    files = {
        **ADOPTED,
        ".scratch/f/issues/01-a.md": "# 01: A\n\nStatus: ready-for-agent\n\nBody.\n",
        ".scratch/f/issues/02-b.md": "# 02: B\n\nStatus: needs-info\n",
        ".scratch/f/issues/03-c.md": "# 03: C\n\nStatus: ready-for-agent" + CONJECTURE,
    }
    root = repository(tmp_path, files)
    offline = route.decide(root, None)
    assert offline.command == f"{route.FEATURE} .scratch/f/issues/03-c.md"
    assert [t.ref for t in route.local_tickets(root)] == [
        ".scratch/f/issues/01-a.md",
        ".scratch/f/issues/03-c.md",
    ]
    assert route.decide(root, FakeGh(issues=None)).command == offline.command


def test_main_prints_the_three_lines_or_json(tmp_path: Path, capsys: Out) -> None:
    root = repository(tmp_path, {"README.md": "# A\n"})
    assert route.main(["route", "--root", str(root), "--offline"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"next: {route.INIT}",
        "why: AGENTS.md does not name the method",
        f"then: {route.TRACKER_SETUP}",
    ]
    assert route.main(["route", "--root", str(root), "--offline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "init"


def test_without_a_forge_the_owner_merges_the_branch(tmp_path: Path) -> None:
    # Why: `/ratchet:init` keeps a repository local when the owner declines the remote, and a
    # pull request then has nowhere to go.
    root = repository(tmp_path, ADOPTED)
    write(root, "notes.md", "x\n")
    assert route.decide(root, None).then == route.NEXT
    branch(root, "feature/4-x")
    write(root, "src/x.py", "x = 1\n")
    assert route.decide(root, None).then.startswith("`just merge <branch>` merges it when")
    commit(root, "feat(x): x")
    step = route.decide(root, FakeGh(pr=None))
    assert step.stage == "merge"
    assert step.command == "`just merge feature/4-x`"
    write(root, "docs/process/binding.md", BINDING.replace("| merge | `just merge` |\n", ""))
    commit(root, "docs(process): a binding from before the merge entry point")
    assert (
        route.decide(root, None).command == "the binding's `merge` entry point with `feature/4-x`"
    )
    assert "then: `git switch main && git merge --no-ff feature/4-x`" in step.why
    assert ".scratch/changes/feature-4-x.md" in step.why


def test_a_ticket_waiting_on_an_open_blocker_is_not_built(tmp_path: Path) -> None:
    files = {
        **ADOPTED,
        ".scratch/f/issues/01-a.md": "# 01: A\n\nStatus: ready-for-human\n",
        ".scratch/f/issues/02-b.md": "# 02: B\n\nBlocked by: 01\n\nStatus: ready-for-agent"
        + CONJECTURE,
        ".scratch/f/issues/03-c.md": "# 03: C\n\nStatus: ready-for-agent\n\nBody.\n",
    }
    root = repository(tmp_path, files)
    assert route.decide(root, None).command == f"{route.CONJECTURE} .scratch/f/issues/03-c.md"
    (root / ".scratch/f/issues/03-c.md").unlink()
    commit(root, "docs(tracker): drop 03")
    held = route.decide(root, None)
    assert held.stage == "blocked"
    assert held.command == 'finish ticket .scratch/f/issues/01-a.md "A", then mark it resolved'
    write(
        root, ".scratch/f/issues/02-b.md", "# 02: B\n\nBlocked by: 09\n\nStatus: ready-for-agent\n"
    )
    commit(root, "docs(tracker): lost blocker")
    assert route.decide(root, None).command == route.SPEC


def test_a_ticket_whose_feature_run_blocked_waits_for_the_default_branch_to_move(
    tmp_path: Path,
) -> None:
    ticket = ".scratch/f/issues/02-b.md"
    files = {**ADOPTED, ticket: "# 02: B\n\nStatus: ready-for-agent" + CONJECTURE}
    root = repository(tmp_path, files)
    assert route.decide(root, None).command == f"{route.FEATURE} {ticket}"
    git(root, "switch", "-q", "-c", "feature/2-b")
    write(root, "docs/features/b/spec.md", "draft\n")
    git(root, "stash", "push", "-q", "-u", "-m", "feature/2-b: blocked run")
    git(root, "switch", "-q", "main")
    held = route.decide(root, None)
    assert held.stage == "blocked-run"
    assert "stash `feature/2-b: blocked run`" in held.command
    assert held.then.startswith(f"{route.FEATURE} {ticket}")
    write(root, "package.json", "{}\n")
    commit(root, "build: the guard edit the run named")
    assert route.decide(root, None).command == f"{route.FEATURE} {ticket}", "main moved on"


def test_only_a_blocked_run_holds_a_ticket(tmp_path: Path) -> None:
    files = {**ADOPTED, ".scratch/f/issues/02-b.md": "# 02: B\n\nStatus: ready-for-agent\n"}
    root = repository(tmp_path, files)
    git(root, "switch", "-q", "-c", "feature/2-b")
    write(root, "x.txt", "draft\n")
    git(root, "stash", "push", "-q", "-u", "-m", "feature/2-b: changes-requested run")
    git(root, "switch", "-q", "main")
    assert route.blocked_runs(root, "main") == {}
    assert route.decide(root, None).stage == "conjecture"


def test_an_unnumbered_ticket_is_held_by_its_slug() -> None:
    ticket = route.preflight.Ticket("idea.md", None, "Idea", "", (), is_open=True)
    assert route.stalled(ticket, {"feature/idea": "feature/idea: blocked run"}) == (
        "feature/idea: blocked run"
    )
    assert route.stalled(ticket, {"feature/other": "feature/other: blocked run"}) is None


def test_a_ticket_whose_branch_is_merged_is_resolved_not_rebuilt(tmp_path: Path) -> None:
    ticket = ".scratch/f/issues/02-b.md"
    files = {**ADOPTED, ticket: "# 02: B\n\nStatus: ready-for-agent" + CONJECTURE}
    root = repository(tmp_path, files)
    git(root, "branch", "feature/2-b-never-built")
    assert route.decide(root, None).stage == "build", "an empty branch is no merge"
    git(root, "switch", "-q", "-c", "feature/2-b")
    write(root, "src/b.py", "b = 1\n")
    commit(root, "feat(b): b")
    git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "feature/2-b", "-m", "Merge branch 'feature/2-b'")
    done = route.decide(root, None)
    assert done.stage == "resolve"
    assert done.command.startswith(f"mark ticket {ticket} resolved")
    assert "`feature/2-b`" in done.why
    write(root, ticket, "# 02: B\n\nStatus: resolved" + CONJECTURE)
    commit(root, "docs(tracker): resolve 02")
    assert route.decide(root, None).stage == "specify"


def test_an_unnumbered_ticket_is_matched_by_its_branch() -> None:
    ticket = route.preflight.Ticket("idea.md", None, "Idea", "", (), is_open=True)
    assert route.finished([ticket], ["feature/idea"]) is not None
    assert route.finished([ticket], ["feature/other"]) is None


def test_open_local_tickets_none_agent_ready_are_triaged_before_a_new_spec(
    tmp_path: Path,
) -> None:
    files = {
        **ADOPTED,
        ".scratch/f/issues/01-a.md": "# 01: A\n\nStatus: resolved\n",
        ".scratch/f/issues/02-b.md": "# 02: B\n\nBlocked by: 03\n\nStatus: needs-triage\n",
        ".scratch/f/issues/03-c.md": "# 03: C\n\nStatus: ready-for-human\n",
        ".scratch/f/issues/04-d.md": "# 04: D\n\nStatus: needs-triage\n",
    }
    root = repository(tmp_path, files)
    step = route.decide(root, None)
    assert step.stage == "triage"
    assert step.command.startswith('agree ticket .scratch/f/issues/04-d.md "D"')
    assert "2 unblocked open ticket(s)" in step.why, "03 for the human, 04; 02 waits on 03"
    (root / ".scratch/f/issues/04-d.md").unlink()
    commit(root, "docs(tracker): drop 04")
    assert route.decide(root, None).command.startswith("agree ticket .scratch/f/issues/03-c.md")
    write(root, ".scratch/f/issues/03-c.md", "# 03: C\n\nStatus: resolved\n")
    write(root, ".scratch/f/issues/02-b.md", "# 02: B\n\nStatus: resolved\n")
    commit(root, "docs(tracker): all resolved")
    assert route.decide(root, None).stage == "specify"
    assert route.decide(root, FakeGh(issues="[]")).stage == "specify", "a tracker is not triaged"


def test_local_ticket_numbers_repeat_across_features_so_branches_match_whole(
    tmp_path: Path,
) -> None:
    beta = ".scratch/f2/issues/01-beta.md"
    files = {
        **ADOPTED,
        ".scratch/f1/issues/01-alpha.md": "# 01: Alpha\n\nStatus: resolved\n",
        beta: "# 01: Beta\n\nStatus: ready-for-agent" + CONJECTURE,
    }
    root = repository(tmp_path, files)
    git(root, "switch", "-q", "-c", "feature/1-alpha")
    write(root, "src/a.py", "a = 1\n")
    commit(root, "feat(a): a")
    git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "feature/1-alpha", "-m", "Merge branch 'feature/1-alpha'")
    assert route.decide(root, None).command == f"{route.FEATURE} {beta}"


def test_a_default_commit_merged_into_a_side_branch_is_no_feature_merge(tmp_path: Path) -> None:
    ticket = ".scratch/f/issues/05-gamma.md"
    files = {**ADOPTED, ticket: "# 05: Gamma\n\nStatus: ready-for-agent" + CONJECTURE}
    root = repository(tmp_path, files)
    git(root, "switch", "-q", "-c", "side")
    write(root, "s.txt", "s\n")
    commit(root, "chore: side")
    git(root, "switch", "-q", "main")
    write(root, "m.txt", "m\n")
    commit(root, "chore: main moves")
    git(root, "branch", "feature/5-gamma")
    git(root, "switch", "-q", "side")
    git(root, "merge", "-q", "--no-ff", "main", "-m", "Merge branch 'main' into side")
    git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "side", "-m", "Merge branch 'side'")
    assert route.merged_features(root, "main") == []
    assert route.decide(root, None).stage == "build"


def test_wontfix_is_closed_and_needs_info_is_not_for_triage(tmp_path: Path) -> None:
    files = {
        **ADOPTED,
        ".scratch/f/issues/01-a.md": "# 01: A\n\nStatus: wontfix\n",
        ".scratch/f/issues/02-b.md": "# 02: B\n\nStatus: needs-info\n",
    }
    root = repository(tmp_path, files)
    assert route.decide(root, None).stage == "specify"


def test_a_detached_head_counts_as_the_default_branch(tmp_path: Path) -> None:
    root = repository(tmp_path, ADOPTED)
    git(root, "switch", "-q", "--detach")
    checkout = route.observe(root)
    assert checkout is not None
    assert checkout.on_default


def test_an_unfinished_init_is_run_again(tmp_path: Path) -> None:
    unfinished = STANZA + "\n## What this is\n\n<One paragraph: purpose, who it is for.>\n"
    root = repository(tmp_path, {"AGENTS.md": unfinished})
    step = route.decide(root, None)
    assert step.command == route.INIT
    assert "purpose" in step.why


def test_a_local_spec_without_tickets_is_split_next(tmp_path: Path) -> None:
    root = repository(tmp_path, {**ADOPTED, ".scratch/settle/spec.md": "# Settle" + CONJECTURE})
    step = route.decide(root, None)
    assert step.command == f"{route.TICKETS} .scratch/settle/spec.md"
    assert step.stage == "specify"
    write(root, ".scratch/settle/issues/01-parse.md", "# Parse\n\nStatus: resolved\n")
    commit(root, "docs: tickets")
    assert route.decide(root, None).command == route.SPEC, "a spec whose tickets exist is split"


def test_a_spec_without_a_conjecture_is_stated_before_it_is_split(tmp_path: Path) -> None:
    # Why: issue 28. to-tickets writes no Conjecture or Operability; the preflight accepts both
    # on the parent, so the spec states them once and every child inherits them.
    spec = ".scratch/settle/spec.md"
    root = repository(tmp_path, {**ADOPTED, spec: "# Settle\n"})
    step = route.decide(root, None)
    assert step.command == f"{route.CONJECTURE} {spec}"
    assert step.then == f"{route.TICKETS} {spec}, then {route.FEATURE} <ticket>"
    assert step.stage == "specify"
    assert "inherit" in step.why
    write(root, spec, "# Settle" + CONJECTURE)
    commit(root, "docs: the conjecture")
    assert route.decide(root, None).command == f"{route.TICKETS} {spec}"


def test_plugin_lines_older_than_the_template_send_the_owner_to_init(tmp_path: Path) -> None:
    template = (route.PLUGIN_ROOT / "skills/init/templates/AGENTS.md").read_text()
    lines = [line for line in template.splitlines() if line.startswith("- **")][:4]
    current = STANZA + "\n" + "\n".join(lines) + "\n"
    root = repository(tmp_path, {**ADOPTED, "AGENTS.md": current})
    assert route.decide(root, None).command != route.INIT
    stale = current.replace(lines[1], lines[1].split("`poteto-mode`")[0] + "refused to an agent.")
    write(root, "AGENTS.md", stale)
    commit(root, "docs: an older stanza")
    step = route.decide(root, None)
    assert step.command == route.INIT
    assert "In what style, with which levers?" in step.why
    assert step.then == route.NEXT


def test_missing_hooks_come_before_everything_a_commit_would_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks_installed, "check", REAL_CHECK)
    root = repository(tmp_path, ADOPTED)
    write(root, "src/x.py", "x = 1\n")
    step = route.decide(root, None)
    assert step.stage == "hooks"
    assert "hooks_install.py" in step.command
    assert step.then == route.NEXT
    assert "skip tier 0" in step.why
    install_hooks(root)
    assert route.decide(root, None).stage == "change"


def test_a_repository_not_yet_adopted_is_not_asked_for_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks_installed, "check", REAL_CHECK)
    root = repository(tmp_path, {"README.md": "# A\n"})
    assert route.decide(root, None).stage == "init"


def test_several_tickets_with_a_conjecture_route_to_the_drain(tmp_path: Path) -> None:
    files = {
        **ADOPTED,
        ".scratch/f/issues/01-a.md": "# 01: A\n\nStatus: ready-for-agent" + CONJECTURE,
        ".scratch/f/issues/02-b.md": "# 02: B\n\nBlocked by: 01\n\nStatus: ready-for-agent"
        + CONJECTURE,
        ".scratch/f/issues/03-c.md": "# 03: C\n\nStatus: ready-for-agent\n\nBody.\n",
    }
    root = repository(tmp_path, files)
    step = route.decide(root, None)
    assert (step.command, step.stage) == (route.FEATURE, "drain")
    assert step.why.startswith("2 agent-ready tickets state a conjecture")
    assert step.then.startswith("the drain's one report")
    (root / ".scratch/f/issues/02-b.md").unlink()
    commit(root, "docs(tracker): one ticket left with a conjecture")
    assert route.decide(root, None).command == f"{route.FEATURE} .scratch/f/issues/01-a.md"


def test_an_answer_that_became_a_habit_is_offered_after_the_three_lines(
    tmp_path: Path, capsys: Out
) -> None:
    root = repository(tmp_path, {"README.md": "# A\n"})
    row = "2026-11-12\tadopt\tadopt.tools\ttooling\tyes\n"
    write(root, ".ratchet/questions.tsv", "date\tskill\tkey\tkind\trecommended\n" + row * 3)
    assert route.main(["route", "--root", str(root), "--offline"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 4
    assert out[3].startswith("also: you have taken the recommendation for `adopt.tools` 3 times")
    assert route.main(["route", "--root", str(root), "--offline", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["also"]) == 1


def test_a_plugin_newer_than_the_pin_is_offered_as_the_upgrade(tmp_path: Path, capsys: Out) -> None:
    pin = f"- Ratchet plugin: 0.0.1 at {'0' * 40}\n"
    root = repository(tmp_path, {**ADOPTED, "docs/process/binding.md": BINDING + pin})
    assert route.main(["route", "--root", str(root), "--offline"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[3].startswith("also: the Ratchet plugin in use is ")
    assert ", newer than the 0.0.1 the binding pins: run `python3 " in out[3]
    assert out[3].endswith("/scripts/upgrade.py`, then change the binding's pin line")
    write(root, "docs/process/binding.md", BINDING)
    assert route.main(["route", "--root", str(root), "--offline"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3


OWES = (
    "- Application types: service\n- Life events: {events}\n\n## Gates\n\n"
    "| Question | Tool | Tier | Threshold source | Entry point |\n| --- | --- | --- | --- | --- |\n"
    "| survives failing dependencies | later: code:outbound-call | 1 | | `just faults` |\n"
    "| backup restores | later: life:irreplaceable-data | weekly | | `just restore` |\n\n"
    "## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n"
    "| outbound-call | `src/**/*.py` | `requests\\.get\\(` |\n"
)


def owing(tmp_path: Path, events: str = "none", **files: str) -> Path:
    binding = BINDING + OWES.format(events=events)
    return repository(tmp_path, {**ADOPTED, "docs/process/binding.md": binding, **files})


def test_an_owed_safeguard_comes_first_and_offers_the_build_beside_it(tmp_path: Path) -> None:
    # Why: ADR 0028. The next change that adds a second call is red, so binding comes first; the
    # owner may be mid-idea, so the step they would otherwise take is named as the alternative.
    root = owing(tmp_path)
    assert route.decide(root, None).command == route.SPEC
    write(root, "src/a.py", "import requests\nrequests.get('http://x')\n")
    commit(root, "feat: the first outbound call")
    step = route.decide(root, None)
    assert step == route.Step(
        route.SAFEGUARD,
        "`outbound-call` has arrived (1 instance) and owes survives failing dependencies."
        f" Or carry on with {route.SPEC}: the first file that brings the kind is free,"
        " a second waits for this",
        route.SPEC,
        "safeguard",
    )
    write(root, "src/b.py", "requests.get('a')\nrequests.get('b')\n")
    commit(root, "feat: two more")
    assert "(3 instances)" in route.decide(root, None).why


def test_a_declared_event_owes_its_rows_and_is_never_red(tmp_path: Path) -> None:
    root = owing(tmp_path, events="irreplaceable-data", **{"GLOSSARY.md": "# <project>\n"})
    step = route.decide(root, None)
    assert step.command == route.SAFEGUARD
    assert step.why == (
        "the declared life event `irreplaceable-data` owes backup restores."
        f" Or carry on with {route.GRILL}: a declared event is never red"
    )
    assert step.then == route.GRILL


def test_a_sign_of_an_undeclared_event_is_proposed_until_the_owner_declines_it(
    tmp_path: Path, capsys: Out
) -> None:
    root = owing(tmp_path, **{"fly.toml": "app = 'a'\n"})
    assert route.event_proposals(root) == [
        (
            "`fly.toml` is a sign that the project is `deployed` (it runs somewhere people other"
            f" than the owner can reach): if it is, type `{route.SAFEGUARD} deployed`; if not yet,"
            " say so and it is not proposed again until a new sign of it appears"
        )
    ]
    assert route.main(["route", "--root", str(root), "--offline", "--decline", "deployed"]) == 0
    assert "not proposed again" in capsys.readouterr().out
    assert route.event_proposals(root) == []
    write(root, "deploy/app.yaml", "kind: x\n")
    assert route.event_proposals(root)[0].startswith("`deploy/app.yaml` is a sign")
    assert route.main(["route", "--root", str(root), "--offline", "--decline", "deployed"]) == 0
    capsys.readouterr()
    assert route.main(["route", "--root", str(root), "--offline"]) == 0
    assert not any(line.startswith("also:") for line in capsys.readouterr().out.splitlines())


def test_a_declared_event_and_an_unadopted_tree_propose_nothing(
    tmp_path: Path, capsys: Out
) -> None:
    root = owing(tmp_path, events="deployed", **{"fly.toml": "app = 'a'\n"})
    assert route.event_proposals(root) == []
    assert route.main(["route", "--root", str(root), "--decline", "deployed"]) == 2
    assert "nothing to decline" in capsys.readouterr().out
    loose = repository(tmp_path / "loose", {"fly.toml": "app = 'a'\n"})
    assert route.event_proposals(loose) == []
    write(root, ".ratchet/events.tsv", "date\tevent\tsign\tanswer\nshort row\n")
    assert route.declined_signs(root) == set()


def test_a_compound_owed_row_is_said_by_its_code_kind(tmp_path: Path) -> None:
    # Why: ADR 0028. `rolls back safely` fires on a deployed project with a stored format; it is
    # described by the kind that arrived, not as a declared event.
    binding = BINDING + OWES.format(events="deployed").replace(
        "later: code:outbound-call", "later: life:deployed and code:outbound-call"
    )
    root = repository(tmp_path, {**ADOPTED, "docs/process/binding.md": binding})
    write(root, "src/a.py", "import requests\nrequests.get('http://x')\n")
    commit(root, "feat: the first outbound call")
    step = route.decide(root, None)
    assert step.why.startswith(
        "`outbound-call` has arrived (1 instance) and owes survives failing dependencies"
    )
    assert "the first file that brings the kind is free" in step.why


def test_a_file_written_for_outside_contributors_is_a_sign_of_depended_on(tmp_path: Path) -> None:
    root = owing(tmp_path, **{"CONTRIBUTING.md": "# How\n", "README.md": "# A\n"})
    [line] = route.event_proposals(root)
    assert line.startswith("`CONTRIBUTING.md` is a sign that the project is `depended-on`")
    assert f"type `{route.SAFEGUARD} depended-on`" in line
    assert route.event_proposals(owing(tmp_path / "plain", **{"README.md": "# A\n"})) == []
