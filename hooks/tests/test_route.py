"""The route: the next command is decided from the checkout, stage by stage."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

import route
from tests.repos import branch, commit, repository, write

Out = pytest.CaptureFixture[str]
STANZA = "# P\n\nLoad the `ratchet:method` skill first.\n"
CONJECTURE = (
    "\n\n## Conjecture\n\nOne script decides the route; the refuter is a wrong command on a"
    " known state. Alternatives: the model guesses (killed: it drifts).\n"
)
BINDING = (
    "# Binding\n\n- Default branch: `main`\n\n| Entry point | Command |\n| --- | --- |\n"
    "| check | `just check` |\n"
)
ADOPTED = {
    "AGENTS.md": STANZA,
    "docs/agents/issue-tracker.md": "GitHub\n",
    "docs/process/binding.md": BINDING,
    "GLOSSARY.md": "# Glossary\n\n**Customer**: who pays.\n",
    "docs/design-brief.md": "# Brief\n",
}


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
    branch(root, "feature/4-x")
    assert route.decide(root, None).command == route.PULL_REQUEST
    assert route.decide(root, FakeGh(pr=None)).stage == "pull-request"
    assert route.decide(root, FakeGh(pr="not json")).stage == "pull-request"
    assert route.decide(root, FakeGh(pr='{"state": "MERGED"}')).stage == "pull-request"
    waiting = route.decide(root, FakeGh(pr='{"state": "OPEN", "url": "https://x/pull/1"}'))
    assert waiting.command == route.WAIT
    assert "https://x/pull/1" in waiting.why


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
