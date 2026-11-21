from __future__ import annotations

import dis
import fcntl
import functools
import json
import os
import pickle
from collections import deque
from dataclasses import replace
from types import CodeType
from typing import TYPE_CHECKING

import pytest

import publish_check
from tests import model_tla
from tests import pipeline_model as model
from tests import pipeline_world as world
from tests.support import PLUGIN_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

PAGE = PLUGIN_ROOT / "docs/model/pipeline.md"

(
    EVIDENCED,
    BOUNDED,
    ORDERED,
    STRICTER,
    CEILINGED,
    MEASURED,
    LOOP_ENDS,
    UNPUBLISHED,
    PUBLISHED_NEVER_APPROVED,
    REPORTED,
    RESUMABLE,
    JUDGED_AGAIN,
    SPLIT,
    SPLIT_UNASKED,
    ASSUMED,
    CRITIC_WAITS,
    CHECK_ASKS_NOTHING,
    NEVER_CRASHES,
    LOST,
    DEPARTED,
    ASKED,
    ROLE_DECIDED,
    UNQUOTED,
    PARKS_CARRIED,
    SECOND_ROUND_KEPT,
    UNREVIEWED,
    COUNTED,
    ROUNDS_KEPT,
) = model.RULES

DIES_FIRST = ["the orchestrator starts a build", "preflight dies"]
RECONCILED_OVER = [
    "the orchestrator judges a branch whose rebase conflicted again",
    "reconcile measures product code over its ceiling",
]
PUBLISHED_APPROVED = [
    "the orchestrator judges a rebased branch again",
    "review-again approves and has published",
]
MEASURED_OVER = [
    "the orchestrator starts a build",
    "preflight is green for a project nobody depends on",
    "spec measures product code over its ceiling",
]
COUNTEREXAMPLES: dict[str, list[str] | None] = {
    EVIDENCED: None,
    BOUNDED: None,
    ORDERED: None,
    STRICTER: None,
    CEILINGED: None,
    MEASURED: None,
    LOOP_ENDS: None,
    UNPUBLISHED: None,
    PUBLISHED_NEVER_APPROVED: PUBLISHED_APPROVED,
    REPORTED: DIES_FIRST,
    RESUMABLE: None,
    JUDGED_AGAIN: None,
    SPLIT: RECONCILED_OVER,
    SPLIT_UNASKED: None,
    ASSUMED: None,
    CRITIC_WAITS: None,
    CHECK_ASKS_NOTHING: None,
    NEVER_CRASHES: None,
    LOST: None,
    DEPARTED: None,
    ASKED: None,
    ROLE_DECIDED: None,
    UNQUOTED: None,
    PARKS_CARRIED: None,
    SECOND_ROUND_KEPT: None,
    UNREVIEWED: None,
    COUNTED: None,
    ROUNDS_KEPT: None,
}
FIXED_OVER = [
    "the orchestrator starts a build",
    "preflight is green for a project that is depended-on",
    "spec fits",
    "ticket-check finds no gap",
    "implement is green",
    "critique finds a major it does not see fixed",
    "fix-1 measures product code over its ceiling",
]
REVIEWED_YOUNG = [
    "the orchestrator starts a build",
    "preflight is green for a project nobody depends on",
    "spec fits",
    "ticket-check finds no gap",
    "implement is green",
    "review requests changes with a blocker of its own",
]
YOUNG_PASS = [*REVIEWED_YOUNG, "fix-1 is green", "review-1 approves"]
YOUNG_PARKED = [
    *REVIEWED_YOUNG,
    "fix-1 is green",
    "review-1 requests changes with a blocker of its own",
]
YOUNG_FIXED_OVER = [*REVIEWED_YOUNG, "fix-1 measures product code over its ceiling"]
YOUNG_APPROVED_ISSUE = [
    *REVIEWED_YOUNG[:-1],
    "review approves with a blocker of its own open",
    "fix-1 is green",
    "review-1 approves",
]


@functools.cache
def graph() -> model.Graph:
    return model.graph()


@functools.cache
def adjacency() -> dict[model.State, list[tuple[str, model.State]]]:
    steps: dict[model.State, list[tuple[str, model.State]]] = {}
    for before, name, after in graph()[1]:
        steps.setdefault(before, []).append((name, after))
    return steps


def shortest_break(name: str) -> list[str] | None:
    rule_holds = model.RULES[name]
    came: dict[model.State, tuple[model.State, str] | None] = {model.INITIAL: None}
    queue = deque([model.INITIAL])
    while queue:
        state = queue.popleft()
        for action, after in adjacency().get(state, []):
            if not rule_holds(state, action, after):
                path, at = [action], came[state]
                while at is not None:
                    path.append(at[1])
                    at = came[at[0]]
                return path[::-1]
            if after not in came:
                came[after] = (state, action)
                queue.append(after)
    return None


def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path() -> None:
    found = {name: shortest_break(name) for name in model.RULES}
    changed = {name: path for name, path in found.items() if path != COUNTEREXAMPLES[name]}
    assert not changed, "shortest breaks that differ from the record:\n" + "\n".join(
        f"{name}: {path}" for name, path in changed.items()
    )


def test_the_page_was_checked_and_drawn_from_the_current_export() -> None:
    assert not (why := model_tla.stale(PAGE, model.export(), model.summary())), why


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


def test_every_answer_of_every_agent_is_given_on_some_run() -> None:
    given = {(model.named(name).role, model.named(name).answer) for _, name, _ in graph()[1]}
    for role, answers in model.ANSWERS.items():
        for answer in answers:
            assert (role, answer) in given, (role, answer)


def test_no_step_of_the_script_turns_on_whether_a_role_published() -> None:
    for state in graph().states:
        unseen = replace(state, published=False)
        for action, after in model.moves(state):
            if action.role is None:
                continue
            plain = model.answered(unseen, replace(action, published=False))
            assert after.published == (state.published or action.published), action.name
            assert replace(after, published=False) == plain, action.name


def runs() -> list[tuple[list[model.Action], model.State]]:
    done: list[tuple[list[model.Action], model.State]] = []
    stack: list[tuple[list[model.Action], model.State]] = [([], model.INITIAL)]
    while stack:
        actions, state = stack.pop()
        if state.ending is not None:
            done.append((actions, state))
        stack.extend(
            ([*actions, action], after)
            for name, after in adjacency().get(state, [])
            if not (action := model.named(name)).published
        )
    return done


def walked(names: list[str]) -> list[model.Action]:
    state, actions = model.INITIAL, []
    for name in names:
        action, state = next(move for move in model.moves(state) if move[0].name == name)
        actions.append(action)
    return actions


def described(
    actions: list[model.Action], expected: model.Ending | None, real: world.Played
) -> str:
    lines = ["run:", *(f"  {action.name}" for action in actions)]
    lines += [f"model: {world.expected_calls(actions)}", f"  {expected}"]
    return "\n".join([*lines, f"real:  {real.calls}", f"  {real.ending}"])


Case = tuple[list[model.Action], model.Ending | None, world.Played]


REPLAYED = pytest.mark.shard_slot(17)
SHARED: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def shared_replay(tmp_path_factory: pytest.TempPathFactory) -> None:
    if os.environ.get("PYTEST_XDIST_WORKER"):
        SHARED[:] = [tmp_path_factory.getbasetemp().parent]


@functools.cache
def played() -> tuple[list[Case], world.Batch]:
    if not SHARED:
        return replay()
    kept = SHARED[0] / "pipeline-replay.pickle"
    with (SHARED[0] / "pipeline-replay.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if kept.exists():
            found: tuple[list[Case], world.Batch] = pickle.loads(kept.read_bytes())  # noqa: S301
            return found
        made = replay()
        part = kept.with_suffix(".part")
        part.write_bytes(pickle.dumps(made, protocol=pickle.HIGHEST_PROTOCOL))
        part.replace(kept)
        return made


def replay() -> tuple[list[Case], world.Batch]:
    cases = [(actions, state.ending) for actions, state in runs()]
    local = [
        max((case for case in cases if case[0][0].answer is start), key=lambda case: len(case[0]))
        for start in (model.Start.BUILD, model.Start.REJUDGE_CONFLICT)
    ]
    batch = world.play([actions for actions, _ in cases], [actions for actions, _ in local])
    together = zip([*cases, *local], batch.played, strict=True)
    return [(actions, ending, real) for (actions, ending), real in together], batch


@REPLAYED
def test_the_real_workflow_does_what_the_model_does_on_every_sequence_of_answers() -> None:
    cases, batch = played()
    differ = [
        (actions, ending, real)
        for actions, ending, real in cases
        if real.calls != world.expected_calls(actions) or real.ending != ending
    ]
    shortest = described(*min(differ, key=lambda run: len(run[0]))) if differ else ""
    assert not differ, f"{len(differ)} of {len(cases)} runs differ; the shortest:\n{shortest}"
    assert set(batch.effects) == set(model.Role), "every agent's prompt was read"
    assert not beyond(batch.effects), beyond(batch.effects)


def beyond(effects: dict[model.Role, frozenset[str]]) -> dict[model.Role, list[str]]:
    excess = {role: sorted(found - model.COMMANDS[role]) for role, found in effects.items()}
    return {role: found for role, found in excess.items() if found}


def test_the_model_check_refuses_a_command_planted_on_a_line_of_its_own(tmp_path: Path) -> None:
    source = world.PIPELINE.read_text(encoding="utf-8")
    anchor = "      git status --porcelain\n"
    assert source.count(anchor) == 1, "the review lists its inputs one command to a line"
    planted = tmp_path / "pipeline.js"
    planted.write_text(source.replace(anchor, f"{anchor}      git rebase main\n"), encoding="utf-8")
    review = [
        model.Action(model.Start.BUILD),
        model.Action(model.Preflight.YOUNG, model.Role.PREFLIGHT),
        model.Action(model.Spec.FITS, model.Role.SPEC),
        model.Action(model.Check.NO_GAP, model.Role.CHECK),
        model.Action(model.Build.GREEN, model.Role.IMPLEMENT),
        model.Action(model.Review.APPROVES, model.Role.REVIEW),
    ]
    assert not beyond(world.play([review]).effects), "the workflow as it stands passes"
    assert beyond(world.play([review], pipeline=planted).effects) == {
        model.Role.REVIEW: ["git rebase"]
    }


def told(actions: list[model.Action]) -> tuple[tuple[str, int], ...]:
    rounds, state = [], model.INITIAL
    for action in actions:
        if action.role is None:
            state = model.STARTS[action.answer]
            continue
        if action.role is model.Role.FIX:
            rounds.append((action.label, model.fix_rounds(state)))
        state = model.answered(state, action)
    return tuple(rounds)


@REPLAYED
def test_on_the_real_workflow_each_fix_agent_is_told_the_rounds_its_loop_has() -> None:
    cases = played()[0]
    assert any(real.told for _, _, real in cases)
    for actions, _, real in cases:
        assert real.told == told(actions), (real.told, [action.name for action in actions])


def ending_on(test: Callable[[model.Action], bool]) -> list[tuple[model.Action, world.Played]]:
    return [(actions[-1], real) for actions, _, real in played()[0] if test(actions[-1])]


@REPLAYED
def test_on_the_real_workflow_a_change_any_measuring_role_finds_over_a_ceiling_stops() -> None:
    over = ending_on(lambda last: last.role in model.MEASURED_AT and last.answer in model.OVER)
    stage = {
        model.Role.SPEC: "the spec",
        model.Role.IMPLEMENT: "the implementation",
        model.Role.FIX: "fix round ",
        model.Role.RECONCILE: "the reconcile",
    }
    assert over
    for last, real in over:
        result = real.raw.get("result") or {}
        assert result.get("verdict") == "blocked", (last.name, real.calls, real.ending)
        too = result["tooLarge"]
        assert (too["kind"], too["stage"].startswith(stage[model.MEASURED_AT[last.role]])) == (
            model.OVER[last.answer].value,
            True,
        ), last.name


@REPLAYED
def test_every_result_the_real_workflow_returns_passes_the_publish_checks_reading(
    tmp_path: Path,
) -> None:
    results = played()[1].results
    assert len(results) >= len(model.Stop) + 2
    for number, result in enumerate(results):
        path = tmp_path / f"result{number}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        publish_check.read_result(path)


@REPLAYED
def test_every_stop_of_the_model_is_reached_on_a_run_of_the_real_workflow() -> None:
    reached = {real.ending.stop for _, _, real in played()[0] if real.ending.stop is not None}
    assert reached == set(model.Stop)


@REPLAYED
def test_on_the_real_workflow_no_review_starts_after_a_finding_is_parked_as_still_owed() -> None:
    parked = [
        (actions, real)
        for actions, _, real in played()[0]
        if real.ending.stop is model.Stop.CARRIED
    ]
    assert parked
    for actions, real in parked:
        labels = [label for label, _ in real.calls]
        assert not [label for label in labels if label.startswith("review")], actions[-1].name
        assert real.calls[-1][0].startswith("critique-"), actions[-1].name
        result = real.raw["result"]
        assert result["stoppedAfter"] == real.calls[-1][0]
        assert result["unresolved"] > 0


@REPLAYED
def test_on_the_real_workflow_a_stop_after_a_role_keeps_what_was_built_and_asks_the_owner() -> None:
    stops = {model.Stop.DEPARTURE, model.Stop.QUESTION_AFTER_BUILD}
    stopped = [(actions, real) for actions, _, real in played()[0] if real.ending.stop in stops]
    assert {real.ending.stop for _, real in stopped} == stops
    for actions, real in stopped:
        result = real.raw["result"]
        assert result["stoppedAfter"] == real.calls[-1][0], actions[-1].name
        assert result["questions"] > 0, actions[-1].name
        if actions[0].answer is model.Start.BUILD:
            assert result["built"] > 0, actions[-1].name


@REPLAYED
def test_on_the_real_workflow_every_dead_agent_ends_in_an_error_that_says_to_resume() -> None:
    dead = ending_on(lambda last: last.answer in model.FAILED)
    assert {last.role for last, _ in dead} == set(model.Role)
    for last, real in dead:
        assert "result" not in real.raw, last.name
        error = real.raw["error"]
        assert error["name"] == "Error", last.name
        assert error["message"].startswith(f"{last.fails_as} agent returned no result"), error
        assert world.RESUME in error["message"], error


@REPLAYED
def test_on_the_real_workflow_a_too_large_stop_ends_there_and_leaves_the_split_unasked() -> None:
    stopped = [
        (actions, real)
        for actions, _, real in played()[0]
        if (real.raw.get("result") or {}).get("tooLarge")
    ]
    assert stopped
    for actions, real in stopped:
        last, too = actions[-1], real.raw["result"]["tooLarge"]
        assert real.calls[-1][0] == last.label, real.calls
        assert last.role in model.MEASURED_AT, last.name
        if last.answer is model.Spec.SPLIT:
            assert too["parts"] > 0, last.name
        for words in world.SPLIT_UNASKED:
            assert words in too["instructions"], (last.name, words)


@functools.cache
def replays() -> dict[str, world.Played]:
    named = {
        "dies": DIES_FIRST,
        "fixed over": FIXED_OVER,
        "young pass": YOUNG_PASS,
        "young parked": YOUNG_PARKED,
        "young fixed over": YOUNG_FIXED_OVER,
        "young approved issue": YOUNG_APPROVED_ISSUE,
        "reconciled over": RECONCILED_OVER,
        "measured over": MEASURED_OVER,
        "published approved": PUBLISHED_APPROVED,
        "approved": [model.read(name) for name in PUBLISHED_APPROVED],
    }
    batch = world.play([walked(names) for names in named.values()])
    return dict(zip(named, batch.played, strict=True))


def test_an_agent_that_dies_ends_the_run_in_an_error_and_no_result() -> None:
    died = replays()["dies"]
    assert died.calls == (("preflight", "Preflight"),)
    assert "result" not in died.raw
    assert died.raw["error"] == {
        "name": "Error",
        "message": "preflight agent returned no result (skipped or died); resume the run to retry",
    }


def test_a_change_a_fix_round_or_a_reconcile_takes_over_the_ceiling_stops_as_too_large() -> None:
    for name, stage, calls in (
        ("fixed over", "fix round 1", ("fix-1", "Fix")),
        ("young fixed over", "fix round 1", ("fix-1", "Fix")),
        ("reconciled over", "the reconcile", ("reconcile", "Reconcile")),
    ):
        real = replays()[name]
        assert real.calls[-1] == calls
        result = real.raw["result"]
        assert result["verdict"] == "blocked"
        assert (result["tooLarge"]["kind"], result["tooLarge"]["stage"]) == ("product", stage)


def test_a_change_measured_over_a_ceiling_stops_with_no_parts_named() -> None:
    real = replays()["measured over"]
    assert real.calls == (("preflight", "Preflight"), ("spec", "Spec"))
    too = real.raw["result"]["tooLarge"]
    assert (too["kind"], too["stage"], too["parts"]) == ("product", "the spec", 0)


def test_a_project_nobody_depends_on_spends_one_fix_pass_and_a_second_review_decides() -> None:
    passed = (("review", "Review"), ("fix-1", "Fix"), ("review-1", "Review"))
    for name, verdict, forced in (
        ("young pass", "approve", []),
        ("young parked", "request_changes", ["1 blocker/major review issue(s)"]),
    ):
        real = replays()[name]
        assert real.calls[-3:] == passed, (name, real.calls)
        result = real.raw["result"]
        assert (result["verdict"], result["forcedBy"], result["iterations"]) == (
            verdict,
            forced,
            1,
        ), name


def test_a_review_that_approves_with_a_blocker_of_its_own_takes_the_fix_pass() -> None:
    real = replays()["young approved issue"]
    assert real.calls[-3:] == (("review", "Review"), ("fix-1", "Fix"), ("review-1", "Review"))
    result = real.raw["result"]
    assert (result["verdict"], result["forcedBy"], result["iterations"]) == ("approve", [], 1)


def test_a_run_in_which_a_role_published_ends_approved_on_the_real_workflow() -> None:
    real = replays()["published approved"]
    assert real.calls == (("review-again", "Review"),)
    assert real.raw["result"]["verdict"] == "approve"
    assert real == replays()["approved"], "the script is handed nothing that says a role published"


def globals_read(code: CodeType) -> set[str]:
    inner = (const for const in code.co_consts if isinstance(const, CodeType))
    loaded = {op.argval for op in dis.get_instructions(code) if op.opname == "LOAD_GLOBAL"}
    return loaded.union(*(globals_read(each) for each in inner))


def test_every_test_that_reads_the_replay_runs_in_the_replays_shard() -> None:
    codes = {
        name: getattr(found, "__wrapped__", found).__code__
        for name, found in globals().items()
        if hasattr(getattr(found, "__wrapped__", found), "__code__")
    }
    readers: set[str] = set()
    reached = {"played"}
    while reached != readers:
        readers = reached
        reached = readers | {name for name, code in codes.items() if globals_read(code) & readers}
    tests = sorted(name for name in readers if name.startswith("test_"))
    assert len(tests) > 1, tests
    marks = {name: getattr(globals()[name], "pytestmark", []) for name in tests}
    unmarked = [name for name, marked in marks.items() if REPLAYED.mark not in marked]
    assert unmarked == [], "each of these plays the replay again in a shard of its own"
