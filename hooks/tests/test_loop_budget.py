import json
from pathlib import Path

import pytest

import loop_budget
import runrecord
from loop_budget import (
    EFFECT,
    OVER,
    WITHIN,
    Forced,
    GateTime,
    Kind,
    NotJudged,
    Place,
    Reading,
    Reason,
    budget_of,
    check_run_times,
    evaluate,
    explained_seconds,
    forced_tag,
    gate_medians,
    has_deep_hold,
    judge,
    pin_changed,
    read_forced,
    trailing_not_judged,
)
from tests.repos import branch, commit, repository, write


def test_judge_decides_in_exact_six_step_order() -> None:
    assert judge(None, Reading(10.0, 2.0, machine_read=True)) == NotJudged(Reason.NO_BUDGET)
    assert judge(None, Reading(None, 1.0, machine_read=False)) == NotJudged(Reason.NO_BUDGET)
    assert judge(100.0, Reading(None, 2.0, machine_read=True)) == NotJudged(Reason.UNREAD)
    assert judge(100.0, Reading(-1.0, 1.0, machine_read=True)) == NotJudged(Reason.UNREAD)
    assert judge(100.0, Reading(float("inf"), 1.0, machine_read=True)) == NotJudged(Reason.UNREAD)
    assert explained_seconds(Reading(None, 2.0, machine_read=True)) == 0.0

    assert judge(100.0, Reading(100.0, 1.0, machine_read=True)) == WITHIN
    assert (
        judge(
            100.0,
            Reading(120.0, 1.0, machine_read=True, forced=Forced("plugin-pin", "ADR 0069", 20.0)),
        )
        == WITHIN
    )
    assert (
        judge(
            100.0,
            Reading(15.0, 1.0, machine_read=True, forced=Forced("plugin-pin", "ADR 0069", 30.0)),
        )
        == WITHIN
    )

    assert judge(100.0, Reading(108.0, 1.09, machine_read=True)) == OVER
    assert judge(100.0, Reading(150.0, 2.0, machine_read=False)) == OVER
    assert judge(100.0, Reading(220.0, 2.0, machine_read=True)) == OVER
    assert judge(100.0, Reading(180.0, 2.0, machine_read=True)) == NotJudged(Reason.MACHINE)
    assert judge(100.0, Reading(200.0, 2.0, machine_read=True)) == NotJudged(Reason.MACHINE)
    assert judge(
        100.0,
        Reading(250.0, 2.0, machine_read=True, forced=Forced("plugin-pin", "ADR 0069", 50.0)),
    ) == NotJudged(Reason.MACHINE)
    assert (
        judge(
            100.0,
            Reading(250.0, 2.0, machine_read=True, forced=Forced("plugin-pin", "ADR 0069", 20.0)),
        )
        == OVER
    )


def test_effect_table_covers_every_kind_place_and_judgement() -> None:
    judgements = (
        WITHIN,
        OVER,
        NotJudged(Reason.MACHINE),
        NotJudged(Reason.NO_BUDGET),
        NotJudged(Reason.UNREAD),
    )
    for place in Place:
        for judgement in judgements:
            assert (Kind.LOOP, place, judgement) in EFFECT
            assert (Kind.SECONDS, place, judgement) in EFFECT
        assert (Kind.WORK, place, WITHIN) in EFFECT
        assert (Kind.WORK, place, OVER) in EFFECT

    assert EFFECT[(Kind.LOOP, Place.LOCAL, WITHIN)].exit_for(0) == 0
    assert EFFECT[(Kind.LOOP, Place.LOCAL, WITHIN)].exit_for(3) == 3
    assert EFFECT[(Kind.LOOP, Place.LOCAL, WITHIN)].annotation is False
    assert EFFECT[(Kind.LOOP, Place.LOCAL, OVER)].exit_for(0) == 1
    assert EFFECT[(Kind.LOOP, Place.LOCAL, OVER)].annotation is False
    for reason in Reason:
        effect = EFFECT[(Kind.LOOP, Place.LOCAL, NotJudged(reason))]
        assert effect.exit_for(2) == 2
        assert effect.annotation is False

    assert EFFECT[(Kind.LOOP, Place.PULL_REQUEST, WITHIN)].exit_for(1) == 0
    assert EFFECT[(Kind.LOOP, Place.PULL_REQUEST, OVER)].exit_for(0) == 1
    for reason in Reason:
        effect = EFFECT[(Kind.LOOP, Place.PULL_REQUEST, NotJudged(reason))]
        assert effect.exit_for(0) == 0
        assert effect.annotation is True

    assert EFFECT[(Kind.LOOP, Place.DEFAULT_BRANCH, WITHIN)].exit_for(1) == 0
    assert EFFECT[(Kind.LOOP, Place.DEFAULT_BRANCH, OVER)].exit_for(0) == 0
    assert EFFECT[(Kind.LOOP, Place.DEFAULT_BRANCH, OVER)].annotation is True
    for reason in Reason:
        effect = EFFECT[(Kind.LOOP, Place.DEFAULT_BRANCH, NotJudged(reason))]
        assert effect.exit_for(0) == 0
        assert effect.annotation is True

    assert EFFECT[(Kind.SECONDS, Place.DEEP, WITHIN)].exit_for(2) == 2
    assert EFFECT[(Kind.SECONDS, Place.DEEP, OVER)].exit_for(0) == 1
    assert EFFECT[(Kind.SECONDS, Place.DEEP, NotJudged(Reason.MACHINE))].annotation is True
    assert EFFECT[(Kind.SECONDS, Place.DEEP, NotJudged(Reason.NO_BUDGET))].annotation is False

    for place in (Place.LOCAL, Place.PULL_REQUEST, Place.DEFAULT_BRANCH):
        assert EFFECT[(Kind.SECONDS, place, OVER)].exit_for(0) == 0
        assert EFFECT[(Kind.SECONDS, place, OVER)].annotation is True

    for place in Place:
        assert EFFECT[(Kind.WORK, place, WITHIN)].exit_for(2) == 2
        assert EFFECT[(Kind.WORK, place, OVER)].exit_for(0) == 1


def test_budget_of_reads_ceilings_loop_seconds_and_ignores_missing_or_bad(tmp_path: Path) -> None:
    assert budget_of(tmp_path) is None
    thresholds = tmp_path / "quality/thresholds.toml"
    thresholds.parent.mkdir(parents=True)
    thresholds.write_text("[ceilings.loop]\nseconds = 120\n", encoding="utf-8")
    assert budget_of(tmp_path) == 120.0
    thresholds.write_text("[ceilings.loop]\nseconds = true\n", encoding="utf-8")
    assert budget_of(tmp_path) is None
    thresholds.write_text("[ceilings.loop]\nseconds = 0\n", encoding="utf-8")
    assert budget_of(tmp_path) is None
    thresholds.write_text("broken = [\n", encoding="utf-8")
    assert budget_of(tmp_path) is None


PIN_A = f"- Ratchet plugin: 0.13.5 at {'a' * 40} tree {'1' * 40}\n"
PIN_B = f"- Ratchet plugin: 0.13.6 at {'b' * 40} tree {'2' * 40}\n"


def pin_repo(tmp_path: Path, *, change_pin: bool) -> Path:
    root = repository(
        tmp_path,
        {
            "docs/process/binding.md": f"# Binding\n\n{PIN_A}",
            "quality/thresholds.toml": "[ceilings.loop]\nseconds = 60\n",
        },
    )
    branch(root, "feat/upgrade")
    if change_pin:
        write(root, "docs/process/binding.md", f"# Binding\n\n{PIN_B}")
    else:
        write(root, "docs/process/binding.md", f"# Binding\n\n{PIN_A}\n## Notes\n\nEdited.\n")
    commit(root, "chore: update binding")
    return root


def test_read_forced_requires_matching_run_tree_and_changed_pin(tmp_path: Path) -> None:
    assert forced_tag("not-a-dict") is None
    assert forced_tag({"cause": "", "record": "ADR 0069"}) is None
    assert forced_tag({"cause": "plugin-pin", "record": 12}) is None

    root = pin_repo(tmp_path / "changed", change_pin=True)
    report = root / ".ratchet/timing.jsonl"
    report.parent.mkdir(parents=True)
    tree = runrecord.tree_id(root)
    run_id = "run-123"
    forced = {"cause": "plugin-pin", "record": "ADR 0069"}
    lines = [
        {
            "kind": "fault",
            "gate": "lint",
            "bite": "a",
            "seconds": 10.0,
            "narrowed": False,
            "forced": forced,
            "run": "other-run",
            "tree": tree,
        },
        {
            "kind": "fault",
            "gate": "lint",
            "bite": "b",
            "seconds": 10.0,
            "narrowed": False,
            "forced": forced,
            "run": run_id,
            "tree": "other-tree",
        },
        {
            "kind": "fault",
            "gate": "lint",
            "bite": "c",
            "seconds": 12.0,
            "narrowed": False,
            "forced": forced,
            "run": run_id,
            "tree": tree,
        },
        {"key": "bite", "seconds": 15.0, "status": "green", "exit": 0, "run": run_id, "tree": tree},
    ]
    report.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    found = read_forced(root, report, run=run_id, tree=tree)
    assert found == Forced("plugin-pin", "ADR 0069", 15.0)
    assert read_forced(root, None, run=run_id, tree=tree) is None
    assert read_forced(root, report, run=runrecord.NO_RUN, tree=tree) is None
    assert read_forced(root, report, run=run_id, tree=runrecord.NO_TREE) is None

    unchanged = pin_repo(tmp_path / "same", change_pin=False)
    same_report = unchanged / ".ratchet/timing.jsonl"
    same_report.parent.mkdir(parents=True)
    same_tree = runrecord.tree_id(unchanged)
    same_report.write_text(
        json.dumps(
            {
                "kind": "fault",
                "gate": "lint",
                "bite": "c",
                "seconds": 12.0,
                "narrowed": False,
                "forced": forced,
                "run": run_id,
                "tree": same_tree,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert read_forced(unchanged, same_report, run=run_id, tree=same_tree) is None

    adopted = repository(tmp_path / "adopt", {"README.md": "hi\n"})
    assert pin_changed(adopted) is False
    branch(adopted, "adopt")
    write(adopted, "docs/process/binding.md", f"# Binding\n\n{PIN_A}")
    commit(adopted, "chore: adopt")
    assert pin_changed(adopted) is False


def test_read_forced_falls_back_to_fault_seconds_and_rejects_other_causes(tmp_path: Path) -> None:
    root = pin_repo(tmp_path, change_pin=True)
    report = root / ".ratchet/timing.jsonl"
    report.parent.mkdir(parents=True)
    tree = runrecord.tree_id(root)
    run_id = "run-456"
    lines = [
        {
            "kind": "fault",
            "gate": "lint",
            "bite": "a",
            "seconds": 8.5,
            "narrowed": False,
            "forced": {"cause": "plugin-pin", "record": "ADR 0069"},
            "run": run_id,
            "tree": tree,
        },
        {
            "kind": "fault",
            "gate": "types",
            "bite": "b",
            "seconds": 4.0,
            "narrowed": False,
            "forced": {"cause": "plugin-pin", "record": "ADR 0069"},
            "run": run_id,
            "tree": tree,
        },
        {"key": "lint", "seconds": 2.0, "status": "green", "exit": 0, "run": run_id, "tree": tree},
        {
            "key": "lint",
            "seconds": "bad",
            "run": run_id,
            "tree": tree,
            "forced": {"cause": "plugin-pin", "record": "ADR 0069"},
        },
    ]
    report.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    assert read_forced(root, report, run=run_id, tree=tree) == Forced(
        "plugin-pin", "ADR 0069", 12.5
    )

    report.write_text(
        json.dumps(
            {
                "kind": "fault",
                "gate": "lint",
                "bite": "a",
                "seconds": 8.5,
                "narrowed": False,
                "forced": {"cause": "other", "record": "ADR 0001"},
                "run": run_id,
                "tree": tree,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert read_forced(root, report, run=run_id, tree=tree) is None
    assert read_forced(tmp_path / "no-git", report, run=run_id, tree=tree) is None


def test_has_deep_hold_checks_workflows_or_no_forge_merge(tmp_path: Path) -> None:
    no_forge = repository(
        tmp_path / "local",
        {
            "docs/process/binding.md": (
                "| Entry point | Command |\n| --- | --- |\n| merge | `just merge` |\n"
            )
        },
    )
    assert has_deep_hold(no_forge) is True

    bare = repository(tmp_path / "bare", {"README.md": "hi\n"})
    assert has_deep_hold(bare) is False

    (bare / ".github/workflows").mkdir(parents=True)
    (bare / ".github/workflows/merge.yml").write_text("name: merge\n", encoding="utf-8")
    assert has_deep_hold(bare) is False
    (bare / ".github/workflows/deep.yml").write_text("name: deep\n", encoding="utf-8")
    assert has_deep_hold(bare) is False
    (bare / ".github/workflows/merge.yml").write_text("name: merge\n--deep\n", encoding="utf-8")
    assert has_deep_hold(bare) is True


def test_history_helpers_classify_streaks_medians_and_check_times() -> None:
    records: list[dict[str, object]] = [
        {"kind": "tier", "seconds": 10.0, "status": "green", "run": "r0"},
        {"key": "lint", "seconds": 2.0, "status": "green", "run": "r0"},
        {"kind": "tier", "seconds": 11.0, "status": "green", "judgement": "within", "run": "r1"},
        {"key": "lint", "seconds": 3.0, "status": "green", "run": "r1"},
        {"key": "lint", "seconds": 99.0, "status": "red", "run": "r2"},
        {"key": "lint", "seconds": "bad", "status": "green", "run": "r1"},
        {"key": "lint", "seconds": 4.0, "status": "green", "run": "now"},
        {"key": "lint", "seconds": 50.0, "status": "green", "run": "r_nj"},
        {
            "kind": "tier",
            "seconds": 50.0,
            "status": "green",
            "judgement": "not judged",
            "reason": Reason.MACHINE.value,
            "run": "r_nj",
        },
        {"key": "slow", "seconds": 40.0, "status": "green", "run": "r_over"},
        {"kind": "tier", "seconds": 40.0, "status": "red", "judgement": "over", "run": "r_over"},
        {
            "kind": "tier",
            "seconds": 12.0,
            "status": "green",
            "judgement": "within",
            "run": runrecord.NO_RUN,
        },
        {"kind": "tier", "seconds": 12.0, "status": "green", "judgement": "within", "run": "now"},
    ]
    medians, has_history = gate_medians(records, current_run="now")
    assert has_history is True
    assert medians == {"lint": 2.5}

    only_over: list[dict[str, object]] = [
        {"key": "slow", "seconds": 40.0, "status": "green", "run": "r_over"},
        {"kind": "tier", "seconds": 40.0, "status": "red", "judgement": "over", "run": "r_over"},
    ]
    over_medians, _ = gate_medians(only_over, current_run="now")
    assert over_medians == {"slow": 40.0}

    assert trailing_not_judged(only_over) is None
    assert trailing_not_judged(
        [
            {"kind": "tier", "judgement": "within"},
            {"kind": "tier", "judgement": "not judged", "reason": Reason.MACHINE.value},
            {"kind": "tier", "seconds": 1.0},
            {"kind": "tier", "judgement": "not judged", "reason": Reason.MACHINE.value},
            {"key": "lint", "seconds": 1.0},
        ]
    ) == (Reason.MACHINE, 2)
    assert (
        trailing_not_judged(
            [{"kind": "tier", "judgement": "not judged", "reason": "unknown-reason"}]
        )
        is None
    )
    assert trailing_not_judged([]) is None

    assert check_run_times(records) == [11.0, 50.0, 40.0, 12.0, 12.0]
    plain: list[dict[str, object]] = [
        {"key": "lint", "seconds": 1.0},
        {"kind": "tier", "seconds": 0.0, "gates": ["test"]},
        {"kind": "tier", "seconds": 5.0, "gates": ["lint"]},
        {"kind": "tier", "seconds": 25.0, "gates": ["lint", "test"]},
    ]
    assert check_run_times(plain, tier1_gates=frozenset({"test"})) == [25.0]
    assert check_run_times(plain) == [5.0, 25.0]


def test_evaluate_formats_all_judgements_tier_fields_and_forced_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = pin_repo(tmp_path / "repo", change_pin=True)
    report = root / ".ratchet/timing.jsonl"
    report.parent.mkdir(parents=True)
    monkeypatch.setenv("RATCHET_RUN", "run-now")

    thresholds = root / "quality/thresholds.toml"
    thresholds.unlink()
    no_budget = evaluate(
        root,
        reading=Reading(12.0, 1.0, machine_read=True),
        report=report,
        tree="tree-1",
        gates=[GateTime("lint", 2.0)],
    )
    assert no_budget.judgement == NotJudged(Reason.NO_BUDGET)
    assert no_budget.tier_fields() == {
        "budget": None,
        "judgement": "not judged",
        "reason": Reason.NO_BUDGET.value,
    }
    assert "loop: not judged: no budget: 12.0s;" in no_budget.line()
    assert "loop.wait; 1 in a row" in no_budget.line()

    thresholds.write_text("[ceilings.loop]\nseconds = 30\n", encoding="utf-8")
    tree = runrecord.tree_id(root)
    report.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "fault",
                        "gate": "lint",
                        "bite": "a",
                        "seconds": 20.0,
                        "narrowed": False,
                        "forced": {"cause": "plugin-pin", "record": "ADR 0069"},
                        "run": "run-now",
                        "tree": tree,
                    }
                ),
                json.dumps(
                    {
                        "key": "bite",
                        "seconds": 20.0,
                        "status": "green",
                        "exit": 0,
                        "run": "run-now",
                        "tree": tree,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    within_forced = evaluate(
        root,
        reading=Reading(35.0, 1.0, machine_read=True),
        report=report,
        tree=tree,
        gates=[GateTime("lint", 2.0), GateTime("bite", 20.0)],
    )
    assert within_forced.judgement == WITHIN
    assert within_forced.line() == (
        "loop: within: 35.0s of 30s (15.0s without 20.0s forced by plugin-pin, ADR 0069)"
    )
    assert within_forced.tier_fields() == {
        "budget": 30.0,
        "judgement": "within",
        "forced": {"cause": "plugin-pin", "record": "ADR 0069", "seconds": 20.0},
    }

    report.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "tier",
                        "seconds": 10.0,
                        "status": "green",
                        "judgement": "within",
                        "run": "r0",
                    }
                ),
                json.dumps(
                    {
                        "kind": "tier",
                        "seconds": 12.0,
                        "status": "green",
                        "judgement": "not judged",
                        "reason": Reason.MACHINE.value,
                        "run": "r1",
                    }
                ),
                json.dumps(
                    {
                        "kind": "tier",
                        "seconds": 12.0,
                        "status": "green",
                        "judgement": "not judged",
                        "reason": Reason.MACHINE.value,
                        "run": "r2",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    by_machine = evaluate(
        root,
        reading=Reading(45.0, 2.0, machine_read=True),
        report=report,
        tree=tree,
        gates=[GateTime("test", 45.0)],
    )
    assert by_machine.judgement == NotJudged(Reason.MACHINE)
    assert "3 in a row" in by_machine.line()
    assert "22.5s" in by_machine.line()

    unread = evaluate(
        root,
        reading=Reading(None, 1.0, machine_read=False),
        report=report,
        tree=tree,
        gates=[],
    )
    assert unread.judgement == NotJudged(Reason.UNREAD)
    assert "loop: not judged: times unread:" in unread.line()
    assert "1 in a row" in unread.line()


def test_evaluate_over_handles_first_run_new_gate_no_gates_and_deep_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "quality").mkdir()
    (root / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 30\n", encoding="utf-8"
    )
    monkeypatch.setenv("RATCHET_RUN", "run-now")

    first_run = evaluate(
        root,
        reading=Reading(40.0, 1.0, machine_read=False),
        gates=[GateTime("lint", 2.0), GateTime("test", 40.0)],
    )
    assert first_run.judgement == OVER
    assert first_run.exit_for(0) == 1
    assert first_run.tier_fields() == {"budget": 30.0, "judgement": "over"}
    assert "slowest: test (40.0s)" in first_run.line()
    assert "busy machine is a possible cause" in first_run.line()
    assert "tier 2" not in first_run.line()
    assert "raise" not in first_run.line().lower()

    empty_gates = evaluate(root, reading=Reading(40.0, 1.0, machine_read=True), gates=[])
    assert "no gate ran" in empty_gates.line()

    report = root / ".ratchet/timing.jsonl"
    report.parent.mkdir()
    history = [
        {"key": "lint", "seconds": 2.0, "status": "green", "exit": 0, "run": "r1", "tree": "t1"},
        {
            "kind": "tier",
            "seconds": 2.0,
            "status": "green",
            "exit": 0,
            "judgement": "within",
            "run": "r1",
            "tree": "t1",
        },
    ]
    report.write_text("\n".join(json.dumps(r) for r in history) + "\n", encoding="utf-8")

    grew_existing = evaluate(
        root,
        reading=Reading(35.0, 1.0, machine_read=True),
        report=report,
        tree="t1",
        gates=[GateTime("lint", 35.0), GateTime("fast_new", 0.01)],
    )
    assert "grew: lint (35.0s vs 2.0s median)" in grew_existing.line()
    assert "fast_new" not in grew_existing.line()

    with_new_gate = evaluate(
        root,
        reading=Reading(35.0, 1.0, machine_read=True),
        report=report,
        tree="t1",
        gates=[GateTime("lint", 2.0), GateTime("new_gate", 35.0)],
    )
    assert "new_gate (35.0s, new)" in with_new_gate.line()

    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/deep.yml").write_text("name: deep\n", encoding="utf-8")
    (root / ".github/workflows/merge.yml").write_text(
        "name: merge\nrun: --deep\n", encoding="utf-8"
    )
    already_over = evaluate(
        root,
        reading=Reading(35.0, 1.0, machine_read=True),
        report=report,
        tree="t1",
        gates=[GateTime("lint", 2.0)],
    )
    assert "already over before this change" in already_over.line()
    assert "or move it to tier 2" in already_over.line()
    assert loop_budget.label_of(WITHIN) == "within"


def test_evaluate_run_supports_places_unread_detail_and_forced_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = pin_repo(tmp_path / "repo", change_pin=True)
    report = root / ".ratchet/timing.jsonl"
    report.parent.mkdir(parents=True)
    monkeypatch.setenv("RATCHET_RUN", "run-pr")
    history = [
        {"key": "lint", "seconds": 5.0, "status": "green", "exit": 0, "run": "r0", "tree": "t0"},
        {"key": "bite", "seconds": 5.0, "status": "green", "exit": 0, "run": "r0", "tree": "t0"},
        {
            "kind": "tier",
            "seconds": 5.0,
            "status": "green",
            "judgement": "within",
            "run": "r0",
            "tree": "t0",
        },
    ]
    report.write_text("\n".join(json.dumps(r) for r in history) + "\n", encoding="utf-8")
    (root / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 20\n", encoding="utf-8"
    )

    unread = loop_budget.evaluate_run(
        root,
        reading=Reading(None, 1.0, machine_read=False, detail="jobs JSON is malformed"),
        report=report,
        options=loop_budget.EvalOptions(place=Place.PULL_REQUEST, tree="t1", run="run-pr"),
    )
    assert unread.judgement == NotJudged(Reason.UNREAD)
    assert unread.exit_for(0) == 0
    assert unread.effect.annotation is True
    assert "times unread: jobs JSON is malformed; 1 in a row" in unread.line()
    assert unread.tier_fields()["missing"] == "jobs JSON is malformed"

    over_pr = loop_budget.evaluate_run(
        root,
        reading=Reading(30.0, 1.0, machine_read=True),
        report=report,
        gates=[GateTime("lint", 5.0), GateTime("bite", 25.0)],
        options=loop_budget.EvalOptions(
            place=Place.PULL_REQUEST, tree="t1", run="run-pr", exclude_proof=True
        ),
    )
    assert over_pr.judgement == OVER
    assert over_pr.exit_for(0) == 1
    assert over_pr.effect.annotation is False
    assert "bite" not in over_pr.line()

    over_main = loop_budget.evaluate_run(
        root,
        reading=Reading(30.0, 1.0, machine_read=True),
        report=report,
        gates=[GateTime("lint", 30.0)],
        options=loop_budget.EvalOptions(place=Place.DEFAULT_BRANCH, tree="t1", run="run-pr"),
    )
    assert over_main.judgement == OVER
    assert over_main.exit_for(0) == 0
    assert over_main.effect.annotation is True
    assert "grew: lint (30.0s vs 5.0s median)" in over_main.line()

    with_wall = loop_budget.evaluate_run(
        root,
        reading=Reading(30.0, 1.0, machine_read=True),
        report=report,
        gates=[GateTime("lint", 30.0)],
        options=loop_budget.EvalOptions(
            tree="t1",
            wall_clock=(
                "parallel: one gate lint (30.0s) set the wall clock while other lanes stood idle"
            ),
        ),
    )
    assert (
        "; one gate lint (30.0s) set the wall clock while other lanes stood idle; grew:"
        in with_wall.line()
    )

    assert loop_budget.forced_from_records(root, [], run="run-pr", tree="t1") is None
