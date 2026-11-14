import json
from pathlib import Path

from first_hour import architecture, audit
from first_hour.persona import Persona, Slice

SYSTEM_Q = (
    "How should it be built? Each choice says what it is made of,"
    " and what changing it later would cost."
)
TOOLS_Q = "Which tools should check the work? Each set names one tool for each kind of check."

RECORD = """---
status: accepted
---
# A small system

## Decision

One program with two parts.

- Chosen by: the owner picked it

## Considered options

- **A server and a store**: offered. More to look after.
- **A script**: set aside. Cannot serve a page.

## Reversal cost

The store's format costs a day to move.

## Refutation criterion

Wrong if the list is slow past a hundred items.
"""

BINDING = """# Binding

## Architecture

- Decision: `docs/adr/0002-the-system.md`

| Part | Path | May depend on |
| --- | --- | --- |
| core | `src/core/**` | nothing |
| web | `src/web/**` | core |

## Gates
"""


def question(text: str, **more: object) -> dict[str, object]:
    return {
        "kind": "question",
        "channel": "dialog",
        "step": "01-adopt",
        "header": "H",
        "question": text,
        **more,
    }


def test_the_architecture_question_in_the_adoption_round_is_found() -> None:
    questions = [
        question("What is it for?", options=["a", "b"]),
        question(
            SYSTEM_Q,
            options=["Plain (Recommended)", "Split", "You decide"],
            recommended="Plain (Recommended)",
            answer="Plain (Recommended)",
            took_recommended=True,
        ),
        question(TOOLS_Q, options=["x"]),
    ]
    found = architecture.asked(questions)
    assert found["asked"]
    assert found["options"] == 3
    assert found["recommended"]
    assert found["enough_options"]
    assert found["batched"]
    assert found["round_questions"] == 3
    assert found["tools_asked"]
    assert found["took_recommended"]
    assert found["answer"] == "Plain (Recommended)"
    assert found["change_questions"] == 0


def test_a_question_that_was_not_asked_reads_as_not_asked() -> None:
    found = architecture.asked(
        [question("What is it for?"), {"channel": "prose", "question": SYSTEM_Q}]
    )
    assert not found["asked"]
    assert found["options"] == 0
    assert not found["recommended"]
    assert not found["batched"]
    assert found["answer"] is None
    assert not found["took_recommended"]
    assert not found["tools_asked"]


def test_one_question_alone_is_not_a_batched_round_and_needs_a_recommendation() -> None:
    found = architecture.asked([question(SYSTEM_Q, options=["only"], recommended=None)])
    assert found["asked"]
    assert not found["batched"]
    assert not found["recommended"]
    assert not found["enough_options"]


def test_a_question_found_by_its_header_alone_counts() -> None:
    found = architecture.asked(
        [{"channel": "dialog", "header": "Architecture", "question": "Which?"}]
    )
    assert found["asked"]


def test_later_questions_to_change_the_architecture_are_counted_apart_from_the_round() -> None:
    questions = [
        question(SYSTEM_Q, options=["a", "b"], recommended="a"),
        question("This needs a new layer for the saved links. Add one?", step="05-feature"),
        question("Move it to another store?", step="05-feature"),
        question("Which colour?", step="05-feature"),
        {"channel": "prose", "step": "06", "question": "Shall I re-architect the parts?"},
    ]
    found = architecture.asked(questions)
    assert found["change_questions"] == 3
    assert len(found["change_list"]) == 3
    # without an architecture round every question outside it is a later one
    assert architecture.asked(questions[1:])["change_questions"] == 3


def test_a_section_runs_to_the_next_heading_of_its_level() -> None:
    text = "# T\n\n## One\n\nbody\n\n### Deep\n\nmore\n\n## Two\n\nother\n"
    assert architecture.section(text, "one") == "body\n\n### Deep\n\nmore"
    assert architecture.section(text, "Two") == "other"
    assert architecture.section(text, "Three") is None


def test_a_complete_record_is_complete_and_each_gap_is_named() -> None:
    record = architecture.decision_record(RECORD)
    assert record == {
        "decision": True,
        "chosen_by": "the owner picked it",
        "options": 2,
        "reversal_cost": True,
        "refutation_criterion": True,
        "complete": True,
    }
    thin = architecture.decision_record(
        RECORD.replace("- Chosen by: the owner picked it", "")
        .replace("- **A script**: set aside. Cannot serve a page.", "")
        .replace("The store's format costs a day to move.", "<what changes>")
        .replace("Wrong if the list is slow past a hundred items.", "")
    )
    assert thin["chosen_by"] is None
    assert thin["options"] == 1
    assert not thin["reversal_cost"]
    assert not thin["refutation_criterion"]
    assert not thin["complete"]
    assert not architecture.decision_record("")["decision"]


def test_the_binding_declares_the_record_and_its_parts() -> None:
    files = {architecture.BINDING: BINDING, "docs/adr/0002-the-system.md": RECORD}
    found = architecture.binding(files)
    assert found["section"]
    assert found["decision"] == "docs/adr/0002-the-system.md"
    assert found["decision_exists"]
    assert found["parts"] == [
        {"part": "core", "path": "`src/core/**`", "depends_on": "nothing"},
        {"part": "web", "path": "`src/web/**`", "depends_on": "core"},
    ]


def test_a_binding_with_no_section_a_missing_record_or_no_table_says_so() -> None:
    assert architecture.binding({}) == {
        "section": False,
        "decision": None,
        "decision_exists": False,
        "parts": [],
    }
    bare = {architecture.BINDING: "## Architecture\n\nnothing yet\n"}
    found = architecture.binding(bare)
    assert found["section"]
    assert found["decision"] is None
    assert not found["decision_exists"]
    assert found["parts"] == []
    missing = {architecture.BINDING: "## Architecture\n\n- Decision: `docs/adr/0009-x.md`\n"}
    assert not architecture.binding(missing)["decision_exists"]


def test_the_parts_table_stops_at_its_end_and_skips_malformed_rows() -> None:
    text = (
        "## Architecture\n\n| Part | Path | May depend on |\n| - | - | - |\n"
        "| a | `a/**` | nothing |\n| b | oops |\n\nAfter | not | a row\n"
    )
    parts = architecture.binding({architecture.BINDING: text})["parts"]
    assert parts == [{"part": "a", "path": "`a/**`", "depends_on": "nothing"}]


def test_features_with_and_without_a_design_section() -> None:
    files = {
        "docs/features/save/spec.md": "# Spec\n\n## Design\n\nPlacement: core.\n",
        "docs/features/list/spec.md": "# Spec\n\n## Design\n\n",
        "docs/features/none/spec.md": "# Spec\n\n## Criteria\n\n1. x\n",
        "docs/features/save/notes.md": "x",
    }
    assert architecture.features(files) == {
        "specs": 3,
        "with_design": 1,
        "without_design": ["list", "none"],
    }


def test_inspect_reads_the_record_the_binding_names_or_the_first_with_options() -> None:
    files = {
        architecture.BINDING: BINDING,
        "docs/adr/0002-the-system.md": RECORD,
        "docs/adr/0001-first.md": "# First\n\n## Decision\n\nx\n",
    }
    found = architecture.inspect(files)
    assert found["adr"]["path"] == "docs/adr/0002-the-system.md"
    assert found["adr"]["complete"]
    assert found["binding"]["section"]
    assert found["features"]["specs"] == 0
    unnamed = architecture.inspect({"docs/adr/0003-a.md": RECORD})
    assert unnamed["adr"]["path"] == "docs/adr/0003-a.md"
    assert unnamed["adr"]["found"]
    none = architecture.inspect({})
    assert none["adr"]["path"] is None
    assert not none["adr"]["found"]
    assert not none["adr"]["complete"]


def test_a_review_result_and_its_design_axis_are_read_from_the_transcript() -> None:
    blobs = [
        json.dumps({"content": 'verdict: "approve"'}),
        json.dumps({"result": {"verdict": "request_changes", "issues": [{"axis": "design"}]}}),
        json.dumps({"text": json.dumps({"verdict": "approve", "issues": [{"axis": "design"}]})}),
        "Judged on axis F and the design axis.",
    ]
    assert architecture.review_axis(blobs) == {"results": 3, "design_issues": 2, "mentions": 2}
    assert architecture.review_axis([]) == {"results": 0, "design_issues": 0, "mentions": 0}


def test_transcript_texts_are_the_messages_bodies() -> None:
    lines = [json.dumps({"msg": {"a": 1}}), "", json.dumps({"msg": "x"})]
    assert architecture.transcript_texts(lines) == ['{"a": 1}', '"x"']


def test_the_files_wanted_are_listed_and_read_through_the_runs_git() -> None:
    tree = {
        "docs/process/binding.md": "b",
        "docs/adr/0001-a.md": "a",
        "docs/features/s/spec.md": "s",
        "src/main.py": "x",
        "docs/adr/gone.md": "z",
    }

    def show(*args: str) -> str | None:
        if args[0] == "ls-tree":
            return "\n".join(tree)
        path = args[1].split(":", 1)[1]
        return tree[path] if path != "docs/features/s/spec.md" else None

    assert audit.files_of(show, "main") == {
        "docs/process/binding.md": "b",
        "docs/adr/0001-a.md": "a",
    }
    assert audit.files_of(lambda *_a: None, "main") == {}


def test_the_architecture_event_joins_the_files_and_the_transcript(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_text(json.dumps({"msg": {"x": 'axis F "design axis"'}}) + "\n", encoding="utf-8")
    event = audit.architecture_event({architecture.BINDING: BINDING}, path)
    assert event["binding"]["section"]
    assert event["reviews"]["mentions"] == 2
    absent = audit.architecture_event({}, tmp_path / "none.jsonl")
    assert absent["reviews"] == {"results": 0, "design_issues": 0, "mentions": 0}


def test_the_leak_event_excuses_what_the_owner_said(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    record = {
        "type": "SystemMessage",
        "msg": {"subtype": "hook_response", "data": {"output": "Use pytest and Rust."}},
    }
    transcript.write_text(json.dumps(record) + "\n\n", encoding="utf-8")
    persona = Persona("p", "an idea", "I want Rust.", Slice("cli", "d", "v", "e"), stack="Rust")
    event = audit.leak_event(persona, ["pytest"], transcript)
    assert event["count"] == 0
    other = Persona("p", "an idea", "no stack", Slice("cli", "d", "v", "e"))
    assert audit.leak_event(other, [], transcript)["names"] == ["Rust", "pytest"]
