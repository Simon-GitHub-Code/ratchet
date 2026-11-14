"""A ticket split by size: its parts inherit the label under a deterministic scope check."""

import pytest

from ratchet_guards import hook, splits
from ratchet_guards.verdict import Decision
from tests.support import LABELS_RECORD, ROOT, FakeFilesystem, FakeRepo, make_guard

PARENT = (
    "# 01: Notes\n\n**Status:** `agent:ready`\n\n## Acceptance criteria\n\n"
    "- [ ] A note is saved\n- [ ] A note is listed newest first\n- [x] A note is deleted\n"
)


def child(*lines: str, parent: str = "01-notes.md", status: str = "agent:ready") -> str:
    criteria = "\n".join(f"- [ ] {line}" for line in lines)
    return (
        f"# 02: Part\n\n**Status:** `{status}`\n\nSplit from: {parent}\n\n"
        f"## Acceptance criteria\n\n{criteria}\n"
    )


def test_a_ticket_is_its_checkbox_lines() -> None:
    assert splits.promises(PARENT) == [
        "A note is saved",
        "A note is listed newest first",
        "A note is deleted",
    ]
    assert splits.promises("No criteria, only prose.\n") == []


@pytest.mark.parametrize(
    ("parts", "named"),
    [
        ([["A note is saved", "A note is listed newest first", "A note is deleted"]], None),
        ([["A note is saved"], ["A note is listed newest first", "A note is deleted"]], None),
        ([["A note is saved", "A note is saved"], ["A note is listed newest first"]], "carried 2"),
        ([["A note is saved"], ["A note is listed newest first"]], "A note is deleted"),
        (
            [
                ["A note is saved"],
                ["A note is listed newest first", "A note is deleted", "A note is shared"],
            ],
            "A note is shared",
        ),
        (
            [["A note is saved."], ["A note is listed newest first", "A note is deleted"]],
            "A note is saved.",
        ),
        (
            [
                ["A note is saved"],
                ["A note is saved"],
                ["A note is listed newest first", "A note is deleted"],
            ],
            "carried 2",
        ),
    ],
    ids=["whole", "split", "twice in one", "dropped", "added", "reworded", "twice in two"],
)
def test_a_split_that_adds_drops_rewords_or_doubles_a_promise_is_refused_by_line(
    parts: list[list[str]], named: str | None
) -> None:
    problems = splits.scope_problems(PARENT, [child(*lines) for lines in parts])
    if named is None:
        assert problems == []
    else:
        assert any(named in problem for problem in problems), problems


def test_prose_outside_the_criteria_is_free() -> None:
    free = child("A note is saved", "A note is listed newest first", "A note is deleted")
    assert splits.scope_problems(PARENT, [free + "\nAnd a whole new paragraph.\n"]) == []


LABEL = "agent:ready"


def test_a_part_may_carry_the_label_only_while_its_parent_does_and_is_no_part() -> None:
    text = child("A note is saved")
    assert splits.refusal(text, PARENT, LABEL) is None
    assert "does not carry" in (
        splits.refusal(text, PARENT.replace("agent:ready", "needs-triage"), LABEL) or ""
    )
    assert "not there" in (splits.refusal(text, None, LABEL) or "")
    assert "names no single ticket" in (splits.refusal("# x\n", PARENT, LABEL) or "")
    assert "not split again" in (splits.refusal(text, child("A note is saved"), LABEL) or "")
    assert "adds a promise" in (splits.refusal(child("Something new"), PARENT, LABEL) or "")
    sibling = child("A note is saved")
    assert "carried 2" in (splits.refusal(text, PARENT, LABEL, siblings=[sibling]) or "")


# --- the guard ---------------------------------------------------------------------------------

TICKET = "/repo/.scratch/f/issues/02-part.md"
SIBLING = "/repo/.scratch/f/issues/03-other.md"


def ruling(
    content: str,
    *,
    parent: str | None = PARENT,
    others: dict[str, str] | None = None,
) -> str | None:
    contents = {"/repo/docs/agents/triage-labels.md": LABELS_RECORD, **(others or {})}
    repo = FakeRepo(
        default_files={} if parent is None else {".scratch/f/issues/01-notes.md": parent}
    )
    guard = make_guard(
        repo=repo,
        filesystem=FakeFilesystem(files=(*FakeFilesystem.files, *contents), contents=contents),
    )
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": TICKET, "content": content},
        "cwd": str(ROOT),
    }
    verdict = hook.decide(payload, guard)
    if verdict is None:
        return None
    assert verdict.decision is Decision.ASK
    return verdict.rule


def test_the_guard_lets_a_part_carry_the_label_its_parent_holds_on_the_default_branch() -> None:
    assert ruling(child("A note is saved")) is None


@pytest.mark.parametrize(
    "case",
    [
        {"content": child("A note is saved"), "parent": None},
        {"content": child("A note is saved"), "parent": PARENT.replace("agent:ready", "triaged")},
        {"content": child("Something new")},
        {"content": child("A note is saved", parent="")},
        {"content": "# 02: Part\n\n**Status:** `agent:ready`\n\nNo parent.\n"},
        {"content": child("A note is saved"), "parent": child("A note is listed newest first")},
    ],
    ids=[
        "no parent",
        "parent unlabelled",
        "added promise",
        "no parent named",
        "no Split from",
        "a part's part",
    ],
)
def test_the_guard_still_asks_where_the_agreement_cannot_be_inherited(
    case: dict[str, str | None],
) -> None:
    kwargs = {k: v for k, v in case.items() if k != "content"}
    assert ruling(str(case["content"]), **kwargs) == "agent-ready-label"  # type: ignore[arg-type]


def test_the_guard_asks_when_a_sibling_already_carries_the_promise() -> None:
    other = {SIBLING: child("A note is saved")}
    assert ruling(child("A note is saved"), others=other) == "agent-ready-label"
    assert ruling(child("A note is listed newest first"), others=other) is None
