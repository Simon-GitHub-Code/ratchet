import json
from pathlib import Path

from first_hour import reduce, transcript
from first_hour.persona import PERSONAS, load

SESSION = "s-1"


def message_events(  # noqa: PLR0917
    s: float, step: str, type_name: str, msg: dict[str, object]
) -> list[dict[str, object]]:
    return [{"s": s, "step": step, **e} for e in transcript.events(type_name, msg)]


def recorded() -> list[dict[str, object]]:
    """A short run as the driver records it: setup, an owner merge, a feature, a usable merge."""
    return [
        {"s": 0, "step": "setup", "kind": "run", "model": "opus", "plugin": {"commit": "abc"}},
        {"s": 1, "step": "01-init", "kind": "step", "action": "type", "text": "/ratchet:init"},
        *message_events(
            2,
            "01-init",
            "SystemMessage",
            {"subtype": "init", "data": {"model": "m", "session_id": SESSION}},
        ),
        {
            "s": 30,
            "step": "01-init",
            "kind": "question",
            "channel": "dialog",
            "class": "product",
            "header": "Purpose",
            "question": "What is it?",
            "recommended": None,
            "took_recommended": False,
            "answer": "A read-later list.",
        },
        {
            "s": 40,
            "step": "01-init",
            "kind": "question",
            "channel": "dialog",
            "class": "default-able",
            "header": "Stack",
            "question": "Which stack?",
            "recommended": "TS (Recommended)",
            "took_recommended": True,
            "answer": "TS (Recommended)",
        },
        *message_events(
            120,
            "01-init",
            "ResultMessage",
            {
                "session_id": SESSION,
                "total_cost_usd": 1.0,
                "num_turns": 3,
                "subtype": "success",
                "result": "done",
            },
        ),
        {"s": 121, "step": "01-init", "kind": "step-end", "wall_s": 120},
        {"s": 121, "step": "00-unknown", "kind": "step-end", "wall_s": 1},
        *message_events(
            121,
            "01-init",
            "SystemMessage",
            {"subtype": "init", "data": {"model": "m", "session_id": SESSION}},
        ),
        {"s": 122, "step": "02-merge", "kind": "step", "action": "paste", "text": "`just merge a`"},
        {"s": 130, "step": "02-merge", "kind": "paste", "command": "just merge a", "exit": 1},
        {
            "s": 131,
            "step": "02-merge",
            "kind": "paste",
            "command": "git switch main && git merge --no-ff a",
            "exit": 0,
        },
        {"s": 140, "step": "02-merge", "kind": "paste", "command": "just check-deep", "exit": 1},
        {
            "s": 141,
            "step": "02-merge",
            "kind": "gates",
            "id": 1,
            "attempt": 1,
            "command": "just check-deep",
            "gates": [
                {"name": "lint", "status": "green", "trigger": ""},
                {"name": "smoke", "status": "later", "trigger": "code:entry-point"},
                {"name": "bite-all", "status": "red", "trigger": ""},
            ],
        },
        {
            "s": 150,
            "step": "02-merge",
            "kind": "gates",
            "id": 1,
            "of": 1,
            "attempt": 2,
            "command": "just check-deep",
            "gates": [{"name": "bite-all", "status": "green", "trigger": ""}],
        },
        {"s": 151, "step": "02-merge", "kind": "step-end", "wall_s": 29},
        {
            "s": 152,
            "step": "02-merge",
            "kind": "merge",
            "branch": "a",
            "how": "hand",
            "owner_text": "merge: owner",
        },
        {
            "s": 153,
            "step": "02-merge",
            "kind": "app",
            "start_command": None,
            "started": False,
            "finding": "no way",
        },
        {
            "s": 160,
            "step": "03-feature",
            "kind": "step",
            "action": "type",
            "text": "/ratchet:feature 01",
        },
        *message_events(
            170,
            "03-feature",
            "TaskStartedMessage",
            {"data": {"task_type": "local_workflow", "workflow_name": "pipeline", "task_id": "w1"}},
        ),
        *message_events(
            171,
            "03-feature",
            "TaskProgressMessage",
            {
                "data": {
                    "workflow_progress": [
                        {"type": "workflow_phase", "index": 1},
                        {"type": "workflow_agent", "agentId": "a1", "label": "spec"},
                        {"type": "workflow_agent", "agentId": "a2", "label": "implement"},
                    ]
                }
            },
        ),
        *message_events(
            172,
            "03-feature",
            "TaskProgressMessage",
            {"data": {"workflow_progress": [{"type": "workflow_agent", "agentId": "a1"}]}},
        ),
        *message_events(
            173,
            "03-feature",
            "TaskStartedMessage",
            {"data": {"task_type": "local_agent", "task_id": "t9", "subagent_type": "poteto"}},
        ),
        *message_events(
            174, "03-feature", "TaskStartedMessage", {"data": {"task_type": "local_bash"}}
        ),
        *message_events(
            175,
            "03-feature",
            "UserMessage",
            {
                "content": [
                    "ToolResultBlock(content='Ratchet guard denied this tool call; no part ran.')",
                    {
                        "_type": "ToolResultBlock",
                        "content": "parallel: triggers: red (exit 1) in 1.0s",
                    },
                ]
            },
        ),
        {"s": 176, "step": "03-feature", "kind": "prompt", "origin": "guard", "decision": "allow"},
        {
            "s": 176.5,
            "step": "03-feature",
            "kind": "prompt",
            "origin": "permission",
            "tool": "Write",
            "reason": "",
            "decision": "allow",
        },
        {
            "s": 177,
            "step": "03-feature",
            "kind": "prompt",
            "origin": "permission",
            "decision": "deny",
        },
        {"s": 178, "step": "03-feature", "kind": "refusal", "why": "pushes", "command": "git push"},
        {"s": 179, "step": "03-feature", "kind": "ask", "text": "please commit"},
        {"s": 180, "step": "03-feature", "kind": "owner-cost", "usd": 0.25},
        *message_events(
            600,
            "03-feature",
            "ResultMessage",
            {
                "session_id": SESSION,
                "total_cost_usd": 6.0,
                "num_turns": 9,
                "subtype": "success",
                "result": "merged",
            },
        ),
        *message_events(600, "03-feature", "AssistantMessage", {"content": []}),
        {"s": 601, "step": "03-feature", "kind": "step-end", "wall_s": 441},
        {
            "s": 602,
            "step": "03-feature",
            "kind": "merge",
            "branch": "feature/01",
            "how": "agent",
            "owner_text": "merged",
        },
        {
            "s": 610,
            "step": "03-feature",
            "kind": "app",
            "start_command": "just dev",
            "source": "justfile",
            "started": True,
            "page_ok": True,
            "slice_ok": True,
            "finding": "the saved link shows on the page",
        },
        {"s": 611, "step": "03-feature", "kind": "usable"},
        {"s": 612, "step": "03-feature", "kind": "usable"},
        {"s": 612, "step": "end", "kind": "stop", "reason": "usable-merged", "detail": ""},
        {"s": 613, "step": "end", "kind": "integrity", "unchanged": True, "files": {}},
        {"s": 613, "step": "end", "kind": "something-new"},
    ]


def test_a_recorded_run_reduces_to_its_measurements() -> None:
    m = reduce.reduce(recorded())
    assert m["meta"]["plugin"] == {"commit": "abc"}
    assert m["meta"]["models_seen"] == ["m"]
    assert m["stop"] == {"reason": "usable-merged", "detail": "", "minute": 10.2}
    assert m["minutes"] == {"total": 10.2, "to_first_merge": 2.5, "to_first_usable_merge": 10.2}
    assert m["app"]["starts"]
    assert m["app"]["slice_works"]
    assert m["app"]["checks"] == 2
    q = m["questions"]
    assert (q["total"], q["product"], q["default_able"]) == (2, 1, 1)
    assert (q["recommended_offered"], q["took_recommended"]) == (1, 1)
    assert q["by_channel"] == {"dialog": 2}
    assert m["prompts"] == {"total": 3, "guard": 2, "permission": 1, "unknown": 0, "denied": 1}
    assert m["guard_denials"] == 1
    assert m["harness_refusals"] == [{"minute": 3.0, "why": "pushes", "command": "git push"}]
    assert m["pastes"]["count"] == 3
    assert m["asks"] == 1
    assert [x["how"] for x in m["merges"]] == ["hand", "agent"]
    assert m["merges"][1]["owner_text"] == "merged"
    assert m["hand_merges"] == 1
    assert m["red_gates"] == [
        {"gate": "bite-all", "command": "just check-deep", "minute": 2.4, "red_on_rerun": False}
    ]
    assert m["session_red_gates"] == {"triggers": 1}
    assert m["gates"]["ran"] == ["bite-all", "lint"]
    assert m["gates"]["later"] == {"smoke": "code:entry-point"}
    assert m["agents"] == {"spawned": 3, "by_via": {"workflow": 2, "Agent": 1}, "workflows": 1}
    assert m["cost_usd"] == {"session": 6.0, "owner": 0.25, "sessions": 1}
    assert [(s["step"], s["minutes"], s["cost_usd"]) for s in m["steps"]] == [
        ("01-init", 2.0, 1.0),
        ("02-merge", 0.5, 0.0),
        ("03-feature", 7.3, 5.0),
    ]
    assert m["integrity"] == {"unchanged": True, "files": {}}


def test_a_red_gate_with_no_rerun_says_so_and_a_run_with_nothing_reduces_to_empty_measures() -> (
    None
):
    run = [
        {
            "s": 0,
            "kind": "gates",
            "id": 1,
            "attempt": 1,
            "command": "c",
            "gates": [{"name": "x", "status": "red"}],
        }
    ]
    assert reduce.reduce(run)["red_gates"][0]["red_on_rerun"] is None
    empty = reduce.reduce([])
    assert empty["minutes"]["to_first_merge"] is None
    assert empty["gates"] == {"ran": [], "later": {}, "none": [], "command": None}
    assert empty["app"] == {"checked": False}
    table = reduce.table(empty)
    assert "| App | not checked |" in table
    assert "| Red gates (red again on rerun) | none |" in table


def test_the_table_names_the_measures_a_person_reads_first() -> None:
    table = reduce.table(reduce.reduce(recorded()))
    assert "| Stopped | usable-merged at 10.2 min |" in table
    assert "| Minutes to first usable merged change | 10.2 |" in table
    assert "| App | starts: True, page: True, slice: True |" in table
    assert "| Questions (product / default-able) | 2 (1 / 1) |" in table
    assert "| Hand merges | 1 of 2 |" in table
    assert "| Red gates (red again on rerun) | bite-all (False) |" in table
    assert "| Gates judged / waiting (`later:`) / not applicable | 2 / 1 / 0 |" in table
    assert "| Cost, product session (owner model) | $6.0 ($0.25) |" in table


def test_an_event_log_reads_back_line_by_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in recorded()[:3]) + "\n\n", encoding="utf-8")
    assert list(reduce.read(path)) == recorded()[:3]


def test_a_cost_the_cli_did_not_report_counts_as_nothing() -> None:
    run = [{"s": 0, "kind": "result", "session": "x", "cost_usd": None}]
    assert reduce.reduce(run)["cost_usd"]["session"] == 0.0


def test_an_answer_that_quotes_the_recommended_label_counts_as_taken() -> None:
    run = [
        {
            "s": 0,
            "kind": "question",
            "class": "product",
            "recommended": "TS (Recommended)",
            "took_recommended": False,
            "answer": "TS (Recommended): it fits my laptop",
        }
    ]
    assert reduce.reduce(run)["questions"]["took_recommended"] == 1


def test_a_prose_question_counts_by_its_class_but_not_as_a_recommendation_offered() -> None:
    run = [
        {
            "s": 0,
            "kind": "question",
            "class": "default-able",
            "channel": "prose",
            "recommended": "the assistant's recommendation",
            "took_recommended": True,
            "answer": "go with what you recommend",
        }
    ]
    q = reduce.reduce(run)["questions"]
    assert (q["default_able"], q["recommended_offered"], q["took_recommended"]) == (1, 0, 0)


def test_the_shipped_persona_loads_from_its_stem_or_its_path() -> None:
    persona = load("read-later")
    assert persona.slice.kind == "html-form"
    assert persona.slice.expect in persona.slice.value
    assert load(str(PERSONAS / "read-later.toml")) == persona
