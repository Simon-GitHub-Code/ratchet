import json
from pathlib import Path

import run
from first_hour import reduce, transcript
from first_hour.persona import PERSONAS, load
from ratchet_guards import hook
from ratchet_guards.verdict import Verdict

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
    assert m["minutes"] == {
        "total": 10.2,
        "to_first_merge": 2.5,
        "to_first_feature_merge": 10.0,
        "to_first_usable_merge": 10.2,
    }
    assert m["app"]["starts"]
    assert m["app"]["slice_works"]
    assert m["app"]["checks"] == 2
    q = m["questions"]
    assert (q["total"], q["product"], q["default_able"]) == (2, 1, 1)
    assert (q["recommended_offered"], q["took_recommended"]) == (1, 1)
    assert q["by_channel"] == {"dialog": 2}
    assert m["prompts"] == {"total": 3, "guard": 2, "permission": 1, "unknown": 0, "denied": 1}
    assert m["guard_denials"] == 1
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
    assert "| App | starts: True, page: True, slice: works |" in table
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


def test_a_call_the_harness_refused_counts_and_a_prompt_takes_its_origin_from_its_reason() -> None:
    run = [
        {"s": 60, "kind": "denied", "tool": "WebFetch", "why": "is off the list", "at": "hook"},
        {"s": 90, "kind": "denied", "tool": "Bash", "why": "pushes to a remote", "at": "prompt"},
        {"s": 95, "kind": "prompt", "tool": "Bash", "decision": "allow", "reason": "Ratchet guard"},
        {"s": 96, "kind": "prompt", "tool": "Bash", "decision": "allow", "reason": "no rule"},
    ]
    metrics = reduce.reduce(run)
    assert metrics["harness_denials"] == [
        {"minute": 1.0, "tool": "WebFetch", "why": "is off the list", "at": "hook"},
        {"minute": 1.5, "tool": "Bash", "why": "pushes to a remote", "at": "prompt"},
    ]
    assert metrics["prompts"]["denied"] == 1
    assert (metrics["prompts"]["guard"], metrics["prompts"]["permission"]) == (1, 1)
    assert "| Calls the harness refused | 2 |" in reduce.table(metrics)


def test_the_guards_own_asks_count_as_the_guards() -> None:
    owner_ask = hook.reason(Verdict("agent-ready-label", "0001-x.md gets the status `agent:ready`"))
    agent_ask = hook.reason(Verdict("merge-default", "git merge feat/x"))
    run = [
        {"s": 1, "kind": "prompt", "tool": tool, "decision": "allow", "reason": reason}
        for tool, reason in (("Write", owner_ask), ("Bash", agent_ask), ("Bash", "no rule"))
    ]
    prompts = reduce.reduce(run)["prompts"]
    assert (prompts["guard"], prompts["permission"]) == (2, 1)


def test_a_write_prompt_with_no_reason_is_the_guard_paths_ask_rule() -> None:
    run = [
        {"s": 1, "kind": "prompt", "tool": tool, "decision": "allow", "reason": ""}
        for tool in ("Edit", "Write", "Bash")
    ]
    prompts = reduce.reduce(run)["prompts"]
    assert (prompts["guard"], prompts["permission"]) == (2, 1)


DEEP = """\
parallel: imports: not run yet (later: code:layer) in 0.1s
parallel: load: not applicable (none: not a service or a job) in 0.1s
parallel: lint: green in 0.1s
parallel: bite: red (exit 3) in 0.1s
"""
WAITING = "parallel: smoke: not run yet (later: code:entry-point) in 0.1s\n"
AS_RECORDED = [{"name": "lint", "status": "green"}, {"name": "bite", "status": "red"}]


def test_reduce_parses_a_recorded_runs_gates_again_from_its_saved_pastes(tmp_path: Path) -> None:
    def paste(s: int, step: str, command: str) -> dict[str, object]:
        return {"s": s, "step": step, "kind": "paste", "command": command, "exit": 0}

    def gate_run(attempt: int, **more: object) -> dict[str, object]:
        run = {"s": 60, "step": "04-m", "kind": "gates", "id": 1, "attempt": attempt}
        return {**run, "command": "just check-deep", "gates": AS_RECORDED, **more}

    recorded = [
        paste(60, "04-m", "just check-deep"),
        gate_run(1),
        gate_run(2, of=1),
        paste(70, "05-n", "just check"),
        paste(80, "05-n", "just merge y"),
        paste(90, "05-n", "just lint"),
        paste(99, "06-o", "just check"),
    ]
    logs = tmp_path / "logs"
    logs.mkdir()
    for name, text in (
        ("04-m-paste-1", f"$ just check-deep\n{DEEP}"),
        ("05-n-paste-2", f"$ just check\n{WAITING}"),
        ("05-n-paste-3", f"$ just merge z\n{WAITING}"),
        ("06-o-paste-5", f"$ just check\n{WAITING}"),
    ):
        (logs / f"{name}.txt").write_text(text, encoding="utf-8")
    log = "".join(json.dumps(e) + "\n" for e in recorded)
    (tmp_path / "events.jsonl").write_text(log, encoding="utf-8")

    assert run.main(["run.py", "reduce", str(tmp_path)]) == 0

    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["gates"] == {
        "command": "just check-deep",
        "minute": 1.0,
        "ran": ["bite", "lint"],
        "later": {"imports": "code:layer"},
        "none": ["load"],
    }
    assert metrics["red_gates"] == [
        {"gate": "bite", "command": "just check-deep", "minute": 1.0, "red_on_rerun": True}
    ]
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8") == log
    again = reduce.reread(recorded, logs)
    assert [(e["s"], e.get("id"), e["attempt"]) for e in again if e["kind"] == "gates"] == [
        (60, 1, 1),
        (60, 1, 2),
        (70, None, 1),
        (99, None, 1),
    ]
    assert list(reduce.reread([], logs)) == []


def test_the_first_merge_of_a_feature_is_the_first_of_a_branch_the_feature_step_builds() -> None:
    merges = [
        {"s": 60, "kind": "merge", "branch": "chore/adopt-ratchet", "how": "entry-point"},
        {"s": 120, "kind": "merge", "branch": "spec/save-and-list", "how": "entry-point"},
        {"s": 150, "kind": "merge", "branch": "docs/spec-save-and-list", "how": "agent"},
        {"s": 180, "kind": "merge", "branch": "records/20261115-010903", "how": "agent"},
        {"s": 300, "kind": "merge", "branch": "feature/1-save", "how": "agent"},
        {"s": 420, "kind": "merge", "branch": "feature/2-list", "how": "agent"},
    ]
    minutes = reduce.reduce(merges)["minutes"]
    assert (minutes["to_first_merge"], minutes["to_first_feature_merge"]) == (1.0, 5.0)
    assert reduce.reduce(merges[:4])["minutes"]["to_first_feature_merge"] is None
    assert reduce.reduce([])["minutes"]["to_first_feature_merge"] is None


ASKED = {
    "kind": "question",
    "channel": "dialog",
    "step": "01-adopt",
    "header": "Build",
    "question": "How should it be built? Each choice says what it is made of.",
    "options": ["Plain (Recommended)", "Split"],
    "recommended": "Plain (Recommended)",
    "answer": "Plain (Recommended)",
    "class": "default-able",
}
REPO = {
    "s": 900,
    "kind": "architecture",
    "adr": {"path": "docs/adr/0002-x.md", "found": True, "complete": True, "options": 2},
    "binding": {"section": True, "parts": [{"part": "core"}, {"part": "web"}]},
    "features": {"specs": 2, "with_design": 1, "without_design": ["b"]},
    "reviews": {"results": 3, "design_issues": 1, "mentions": 2},
}


def test_the_architecture_measures_join_the_questions_and_the_repository() -> None:
    later = {**ASKED, "step": "05-feature", "question": "This needs a new layer. Add one?"}
    metrics = reduce.reduce([ASKED, later, REPO])
    found = metrics["architecture"]
    assert found["asked"]
    assert found["recommended"]
    assert found["change_questions"] == 1
    assert found["read"]
    assert found["parts_declared"] == 2
    assert found["adr"]["complete"]
    assert found["features"]["with_design"] == 1
    assert found["reviews"]["mentions"] == 2
    text = reduce.table(metrics)
    assert "| Architecture asked (options / recommended / one round) | 2 / yes / no |" in text
    assert (
        "| Architecture record landed | yes (options 2, reversal cost no, refutation no) |" in text
    )
    assert "| Parts declared in the binding | 2 |" in text
    assert "| Features with a Design section | 1 of 2 |" in text
    assert "| Reviews seen / design-axis issues / axis F mentions | 3 / 1 / 2 |" in text
    assert "| Architecture-change questions after adoption | 1 |" in text


def test_a_run_whose_repository_was_not_read_says_so_in_every_architecture_row() -> None:
    metrics = reduce.reduce([])
    assert metrics["architecture"]["read"] is False
    assert metrics["architecture"]["parts_declared"] == 0
    text = reduce.table(metrics)
    assert "| Architecture asked (options / recommended / one round) | not asked |" in text
    assert "| Architecture record landed | not read |" in text
    assert "| Parts declared in the binding | not read |" in text
    assert "| Features with a Design section | not read |" in text
    assert "| Stack names the plugin introduced (owner never said) | not read |" in text
    missing = reduce.reduce([{"s": 1, "kind": "architecture", "adr": {"found": False}}])
    assert "| Architecture record landed | no |" in reduce.table(missing)
    assert missing["architecture"]["reviews"] == {}


def test_the_stack_names_the_plugin_introduced_are_counted_and_named() -> None:
    event = {
        "s": 5,
        "kind": "stack-leaks",
        "count": 2,
        "names": ["Go", "pytest"],
        "items": [{"name": "Go", "source": "hook", "excerpt": "go test", "count": 1}],
    }
    metrics = reduce.reduce([event])
    assert metrics["stack_leaks"]["count"] == 2
    row = "| Stack names the plugin introduced (owner never said) | 2 (Go, pytest) |"
    assert row in reduce.table(metrics)
    clean = reduce.reduce([{**event, "count": 0, "names": []}])
    assert "owner never said) | 0 (none) |" in reduce.table(clean)
