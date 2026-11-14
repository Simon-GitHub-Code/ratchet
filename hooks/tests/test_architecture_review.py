"""The architecture review: an arrival that reopens the architecture owes a review (ADR 0052)."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import application_types
import conformance
import drain
import hooks_installed
import queue_notes
import route
import triggers
from tests.repos import branch, commit, git, repository, write
from tests.test_route import ADOPTED
from tests.test_route import BINDING as ROUTED_BINDING

if TYPE_CHECKING:
    import feature_preflight as preflight

Out = pytest.CaptureFixture[str]
BINDING = "docs/process/binding.md"
REVIEW_ROW = (
    "| architecture reviewed when its shape changes | `ratchet` plugin `scripts/triggers.py` | 1"
    " | | `just triggers` |"
)
LATER_STORE = "| old data still loads | later: code:stored-format | 1 | | `just data-compat` |"
TABLE = """
## Triggers

| Kind | Paths | Pattern |
| --- | --- | --- |
| stored-format | `src/**/*.py` | `^TABLE = ` |
| config-read | `src/**/*.py` | `getenv\\(` |
"""
STORE = "TABLE = 'links'\n"
SETTING = "import os\nos.getenv('X')\n"


def binding(*rows: str, events: str = "none") -> str:
    return (
        "# Binding\n\n- Application types: service\n"
        f"- Life events: {events}\n\n## Gates\n\n"
        "| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        + "\n".join(["| format | a tool | 0 | | `just fmt` |", *rows])
        + "\n"
        + TABLE
    )


def project(
    tmp_path: Path, files: dict[str, str] | None = None, *rows: str, events: str = "none"
) -> Path:
    """An adopted repository whose binding asks the review, on ``main``, then a change branch."""
    text = binding(REVIEW_ROW, *rows, events=events)
    root = repository(tmp_path, {BINDING: text, "README.md": "# A\n", **(files or {})})
    branch(root)
    return root


def run(root: Path) -> int:
    return triggers.main(["triggers", "--root", str(root)])


def land(root: Path, subject: str) -> None:
    """Commit the work tree and make it the default branch, as a landed change does."""
    commit(root, subject)
    git(root, "branch", "-f", "main")


def record(*keys: str, outcome: str = "holds", options: int = 0, refuted: bool = True) -> str:
    """A decision record that reviews ``keys``."""
    listed = "".join(
        f"- Option {n}: a way to build it, and its cost to undo.\n" for n in range(options)
    )
    return (
        "# The way it is built holds now that it keeps data\n\n"
        f"- Architecture review: {', '.join(f'`{key}`' for key in keys)}\n"
        f"- Outcome: {outcome}\n- Decided: 2026-11-14\n\n"
        "## Decision\n\nThe store module alone reads and writes the links, as ADR 0001 says.\n\n"
        + (f"## Considered options\n\n{listed}\n" if options else "")
        + (
            "## Refutation criterion\n\nA second module that writes the links shows it wrong.\n"
            if refuted
            else ""
        )
    )


def arrived(tmp_path: Path) -> Path:
    """A repository where stored data arrived after the reviews started, landed."""
    root = project(tmp_path)
    write(root, "src/store.py", STORE)
    land(root, "feat(core): keep the links")
    return root


def test_an_arrival_that_reopens_the_architecture_is_free_and_owes_a_review(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "src/store.py", STORE)
    assert run(root) == 0
    out = capsys.readouterr().out
    assert (
        "triggers: note: 'stored-format' has arrived (src/store.py:1) and reopens the architecture"
        " (the project now keeps data:"
    ) in out
    assert "Run /ratchet:architecture next to review it before another is added (ADR 0052)" in out
    assert triggers.reviews_owed(root) == [
        triggers.Owed("architecture review", "code:stored-format", "stored-format", 1)
    ]


def test_another_instance_while_the_review_is_owed_is_red(tmp_path: Path, capsys: Out) -> None:
    root = arrived(tmp_path)
    capsys.readouterr()
    write(root, "src/more.py", STORE)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert (
        "triggers: architecture review owed: 'stored-format' gained 1 instance(s) (src/more.py:1)"
        " while the base already held 1 and the review it reopens has not landed: no decision"
        " record names `code:stored-format`. Run /ratchet:architecture and land its decision"
        " record before adding more (ADR 0052)"
    ) in out
    assert "triggers: red" in out


def test_a_review_in_the_same_change_as_the_instance_does_not_count(tmp_path: Path) -> None:
    # Why: the review lands on its own, so an unattended build cannot decide the architecture
    # beside the code that grows under it.
    root = arrived(tmp_path)
    write(root, "docs/adr/0002-holds.md", record("code:stored-format"))
    assert triggers.reviews_owed(root) == []
    write(root, "src/more.py", STORE)
    assert run(root) == 1


def test_a_landed_review_clears_the_kind(tmp_path: Path, capsys: Out) -> None:
    root = arrived(tmp_path)
    write(root, "docs/adr/0002-holds.md", record("code:stored-format"))
    assert run(root) == 0
    land(root, "docs(adr): architecture review for stored data")
    write(root, "src/more.py", STORE)
    capsys.readouterr()
    assert run(root) == 0
    assert "architecture" not in capsys.readouterr().out
    assert triggers.reviews_owed(root) == []


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (record("code:stored-format", refuted=False), "has no '## Refutation criterion' section"),
        (
            record("code:stored-format", outcome="changes", options=1),
            "changes the architecture and lists 1 option(s) under '## Considered options'",
        ),
        (record("code:stored-format", outcome="maybe"), "has no '- Outcome: holds' or"),
        (record("code:stored-format").replace("- Decided: 2026-11-14\n", ""), "'- Decided:"),
        (record("code:listener"), "no decision record names `code:stored-format`"),
    ],
)
def test_a_record_that_is_not_a_whole_review_does_not_count_and_says_why(
    tmp_path: Path, capsys: Out, *, text: str, why: str
) -> None:
    root = arrived(tmp_path)
    write(root, "docs/adr/0002-review.md", text)
    land(root, "docs(adr): a review")
    write(root, "src/more.py", STORE)
    capsys.readouterr()
    assert run(root) == 1
    assert why in capsys.readouterr().out


def test_a_record_that_changes_the_architecture_counts_with_its_options(tmp_path: Path) -> None:
    root = arrived(tmp_path)
    changes = record("code:stored-format", outcome="changes", options=2)
    write(root, "docs/adr/0002-changes.md", changes)
    land(root, "docs(adr): a review")
    write(root, "src/more.py", STORE)
    assert run(root) == 0


def test_a_declared_event_owes_a_review_and_is_never_red(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    text = (root / BINDING).read_text(encoding="utf-8")
    write(root, BINDING, text.replace("- Life events: none", "- Life events: deployed"))
    land(root, "chore(method): declare deployed")
    capsys.readouterr()
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "note: life event 'deployed' is declared and reopens the architecture" in out
    assert [review.trigger for review in triggers.reviews_owed(root)] == ["life:deployed"]
    write(root, "docs/adr/0002-deployed.md", record("life:deployed"))
    assert triggers.reviews_owed(root) == []


def test_an_event_declared_when_the_reviews_started_was_reviewed_by_the_adoption(
    tmp_path: Path,
) -> None:
    root = project(tmp_path, events="deployed, irreplaceable-data")
    assert triggers.reviews_owed(root) == []


def test_a_kind_already_there_when_the_reviews_started_is_reviewed_by_the_adoption(
    tmp_path: Path, capsys: Out
) -> None:
    # Why: an adopted repository must not turn red for what it already had. Before the upgrade
    # adds the row nothing is asked; the commit that adds it is where the reviews start.
    root = repository(tmp_path, {BINDING: binding(), "src/store.py": STORE})
    branch(root)
    write(root, "src/more.py", STORE)
    assert run(root) == 0
    assert "architecture" not in capsys.readouterr().out
    assert triggers.reviews_owed(root) == []
    (root / "src/more.py").unlink()
    write(root, BINDING, binding(REVIEW_ROW))
    land(root, "chore(method): upgrade the plugin")
    assert triggers.reviews_owed(root) == []
    write(root, "src/more.py", STORE)
    assert run(root) == 0
    assert "architecture" not in capsys.readouterr().out


def test_a_kind_that_was_there_at_the_start_and_returns_is_still_reviewed(tmp_path: Path) -> None:
    root = project(tmp_path, {"src/store.py": STORE})
    (root / "src/store.py").unlink()
    land(root, "refactor(core): keep nothing")
    write(root, "src/store.py", STORE)
    write(root, "src/more.py", STORE)
    assert run(root) == 0


def test_a_kind_that_arrived_since_the_start_and_returns_is_owed(
    tmp_path: Path, capsys: Out
) -> None:
    root = arrived(tmp_path)
    (root / "src/store.py").unlink()
    land(root, "refactor(core): keep nothing")
    capsys.readouterr()
    write(root, "src/store.py", STORE)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "architecture review owed: 'stored-format' gained 1 instance(s)" in out
    assert "returning, not arriving" in out


def test_a_kind_that_does_not_reopen_the_architecture_owes_no_review(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    write(root, "src/settings.py", SETTING)
    assert run(root) == 0
    land(root, "feat(core): read a setting")
    write(root, "src/other.py", SETTING)
    assert run(root) == 0
    assert "architecture" not in capsys.readouterr().out
    assert triggers.reviews_owed(root) == []


def test_rows_and_the_review_are_owed_side_by_side_and_each_says_its_own(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path, None, LATER_STORE)
    write(root, "src/store.py", STORE)
    land(root, "feat(core): keep the links")
    write(root, "src/more.py", STORE)
    capsys.readouterr()
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "triggers: 'stored-format' gained 1 instance(s)" in out
    assert "these rows are still `later:`: old data still loads" in out
    assert "triggers: architecture review owed: 'stored-format' gained 1 instance(s)" in out


def test_json_lists_the_reviews_owed_with_what_each_reopens(tmp_path: Path, capsys: Out) -> None:
    root = arrived(tmp_path)
    capsys.readouterr()
    assert triggers.main(["triggers", "--root", str(root), "--reviews"]) == 0
    (owing,) = json.loads(capsys.readouterr().out)
    reopens = application_types.load().architecture["code:stored-format"]
    assert owing == {
        "question": "architecture review",
        "trigger": "code:stored-format",
        "name": "stored-format",
        "count": 1,
        "reopens": reopens,
    }


def test_every_trigger_that_reopens_the_architecture_names_a_kind_or_an_event() -> None:
    catalogue = application_types.load()
    assert catalogue.architecture
    for trigger in catalogue.architecture:
        assert catalogue.trigger_problem(trigger) is None, trigger
    assert set(application_types.code_kinds(" and ".join(catalogue.architecture))) <= (
        catalogue.kinds_required({"library"})
    )
    assert catalogue.kinds_required(set()) == set()


def test_the_review_row_is_the_template_s_and_the_plugin_s_own_gate() -> None:
    template = conformance.template_binding()
    assert REVIEW_ROW.split(" | 1 |", maxsplit=1)[0] in template
    assert triggers.reviews_bound(template)
    unbound = binding(REVIEW_ROW.replace("`ratchet` plugin `scripts/triggers.py`", "none: no"))
    assert not triggers.reviews_bound(unbound)


ROUTED = (
    "- Application types: service\n- Life events: none\n\n## Gates\n\n"
    "| Question | Tool | Tier | Threshold source | Entry point |\n| --- | --- | --- | --- | --- |\n"
    f"{REVIEW_ROW}\n{LATER_STORE}\n" + TABLE
)


def test_the_route_names_the_review_before_the_safeguards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks_installed, "check", lambda _root: None)
    root = repository(tmp_path, {**ADOPTED, BINDING: ROUTED_BINDING + ROUTED})
    assert route.decide(root, None).command == route.SPEC
    write(root, "src/store.py", STORE)
    commit(root, "feat: keep the links")
    step = route.decide(root, None)
    assert step.command == route.ARCHITECTURE
    assert step.stage == "architecture"
    assert step.why.startswith(
        "`stored-format` has arrived (1 instance) and reopens the architecture: the project now"
        " keeps data:"
    )
    assert step.why.endswith(
        f"Or carry on with {route.SPEC}: the change that brought the kind was free, one that"
        " adds another waits for this"
    )
    assert step.then == route.SAFEGUARD
    write(root, "docs/adr/0002-holds.md", record("code:stored-format"))
    commit(root, "docs(adr): architecture review for stored data")
    assert route.decide(root, None).command == route.SAFEGUARD


REVIEW_LINE = (
    "triggers: architecture review owed: 'stored-format' gained 1 instance(s) (src/b.py:1) while"
    " the base already held 1 and the review it reopens has not landed"
)


def test_the_drain_holds_a_ticket_for_an_owed_review_until_it_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result: dict[str, object] = {"checkOutput": REVIEW_LINE}
    assert drain.owed_kinds(result) == []
    assert drain.owed_kinds(result, drain.REVIEW_RED) == ["stored-format"]
    assert any(route.ARCHITECTURE in reason for reason in drain.reasons_of(result))
    note: dict[str, object] = {"owes": [], "reviews": ["stored-format"]}
    owing = [triggers.Owed("architecture review", "code:stored-format", "stored-format", 1)]
    monkeypatch.setattr(triggers, "owed", lambda _root: [])
    monkeypatch.setattr(triggers, "reviews_owed", lambda _root: owing)
    assert not queue_notes.safeguarded(tmp_path, note)
    monkeypatch.setattr(triggers, "reviews_owed", lambda _root: [])
    assert queue_notes.safeguarded(tmp_path, note)
    hold = queue_notes.Hold([], [], [], ["stored-format"])
    ticket = cast("preflight.Ticket", SimpleNamespace(ref="#2"))
    assert route.releases(hold, ticket, "main") == [f"run {route.ARCHITECTURE} for stored-format"]


def test_conformance_asks_for_a_row_of_each_kind_that_reopens_the_architecture(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, {BINDING: binding(REVIEW_ROW)})
    problems = [str(finding) for finding in conformance.check_triggers(root)]
    assert any(
        "the Triggers table has no row for kind 'access-control', whose arrival reopens the"
        " architecture (ADR 0052)" in problem
        for problem in problems
    ), problems
