"""One pipeline run's rules: on every run its model allows, then on the real workflow.

The answers are finite and the workflow is deterministic given them, so nothing here samples: every
sequence of abstract answers up to the fix loop's bound runs on the model and on the real
``workflows/pipeline.js``, all of them in one node process.
"""

from __future__ import annotations

import functools
from collections import deque
from typing import TYPE_CHECKING

from tests import pipeline_model as model
from tests import pipeline_world as world

if TYPE_CHECKING:
    from collections.abc import Callable

(
    EVIDENCED,
    BOUNDED,
    ORDERED,
    STRICTER,
    CEILINGED,
    MEASURED,
    CHECKED_LATE,
    LOOP_ENDS,
    UNPUBLISHED,
    REPORTED,
    RESUMABLE,
    JUDGED_AGAIN,
    SPLIT,
    ASSUMED,
    CRITIC_WAITS,
    CHECK_ASKS_NOTHING,
    NEVER_CRASHES,
    LOST,
) = model.RULES

# Why: a rule the workflow does not keep is pinned here by the shortest run from the start to a
# step that breaks it, so the break is a recorded fact and not a red test.
DIES_FIRST = ["the orchestrator starts a build", "preflight dies"]
RECONCILED_OVER = [
    "the orchestrator judges a branch whose rebase conflicted again",
    "reconcile measures product code over its ceiling",
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
    # 5 breaks on a reconcile over a ceiling, because the script does not measure a fix round or
    # a reconcile. 5a holds. It is pipeline.md step 3: "After the spec and again after the
    # implementation the script reads the gate's numbers." 5b holds too. Step 7 forces
    # request_changes on a red check from the last builder or the review, and tiers.md puts
    # change size in tier 1, which `check` runs in an adopted project.
    CEILINGED: RECONCILED_OVER,
    MEASURED: None,
    CHECKED_LATE: None,
    LOOP_ENDS: None,
    UNPUBLISHED: None,
    # 8 breaks on a dead agent, because the script raises and returns no result. 8a holds. It is
    # ADR 0023 decision 2: "One run per ticket also keeps its own run to resume, so a ticket that
    # dies is run again alone." The script's error says to resume the run.
    REPORTED: DIES_FIRST,
    RESUMABLE: None,
    JUDGED_AGAIN: None,
    SPLIT: MEASURED_OVER,
    ASSUMED: None,
    CRITIC_WAITS: None,
    CHECK_ASKS_NOTHING: None,
    NEVER_CRASHES: None,
    LOST: None,
}
# Why: rule 5's break and its twin in the fix loop, carried on to a review, so the real workflow
# shows the approval they end in.
FIXED_OVER_AND_APPROVED = [
    "the orchestrator starts a build",
    "preflight is green for a project that is depended-on",
    "spec fits",
    "ticket-check finds no gap",
    "implement is green",
    "critique finds a major it does not see fixed",
    "fix-1 measures product code over its ceiling",
    "critique-1 finds nothing",
    "review approves",
]
RECONCILED_OVER_AND_APPROVED = [*RECONCILED_OVER, "review-again approves"]


@functools.cache
def graph() -> tuple[frozenset[model.State], list[model.Edge]]:
    return model.graph()


@functools.cache
def adjacency() -> dict[model.State, list[tuple[str, model.State]]]:
    steps: dict[model.State, list[tuple[str, model.State]]] = {}
    for before, name, after in graph()[1]:
        steps.setdefault(before, []).append((name, after))
    return steps


def shortest_break(name: str) -> list[str] | None:
    """The fewest actions from the start to a step that breaks the rule, or ``None``."""
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


def runs() -> list[tuple[list[model.Action], model.State]]:
    """Every run from the start to an end, with the state it ends in."""
    done: list[tuple[list[model.Action], model.State]] = []
    stack: list[tuple[list[model.Action], model.State]] = [([], model.INITIAL)]
    while stack:
        actions, state = stack.pop()
        if state.ending is not None:
            done.append((actions, state))
        stack.extend(
            (([*actions, model.named(name)], after) for name, after in adjacency().get(state, []))
        )
    return done


def walked(names: list[str]) -> list[model.Action]:
    """The actions a named run takes, each checked to be open where the run takes it."""
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


@functools.cache
def played() -> tuple[list[Case], world.Batch]:
    """Every run of the model with the ending it expects, and what the real workflow did on it."""
    cases = [(actions, state.ending) for actions, state in runs()]
    # Why: a local ticket file changes what the roles that read the ticket are told to run, so the
    # longest build and the longest rejudge run once more reading one.
    local = [
        max((case for case in cases if case[0][0].answer is start), key=lambda case: len(case[0]))
        for start in (model.Start.BUILD, model.Start.REJUDGE_CONFLICT)
    ]
    batch = world.play([actions for actions, _ in cases], [actions for actions, _ in local])
    together = zip([*cases, *local], batch.played, strict=True)
    return [(actions, ending, real) for (actions, ending), real in together], batch


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
    for role, commands in batch.effects.items():
        assert commands <= model.COMMANDS[role], (role, sorted(commands - model.COMMANDS[role]))


def ending_on(test: Callable[[model.Action], bool]) -> list[tuple[model.Action, world.Played]]:
    """Each real run whose last answer passes ``test``, with that answer."""
    return [(actions[-1], real) for actions, _, real in played()[0] if test(actions[-1])]


def test_on_the_real_workflow_a_change_the_spec_or_the_implementation_measures_over_stops() -> None:
    over = ending_on(lambda last: last.role in model.MEASURED_AT and last.answer in model.OVER)
    stage = {model.Role.SPEC: "the spec", model.Role.IMPLEMENT: "the implementation"}
    assert over
    for last, real in over:
        result = real.raw.get("result") or {}
        assert result.get("verdict") == "blocked", (last.name, real.calls, real.ending)
        too = result["tooLarge"]
        expected = (model.OVER[last.answer].value, stage[model.MEASURED_AT[last.role]])
        assert (too["kind"], too["stage"]) == expected, last.name


def test_on_the_real_workflow_a_fix_or_reconcile_over_is_approved_only_on_green_checks() -> None:
    approved = [
        actions
        for actions, _, real in played()[0]
        if any(a.role in model.LATE and a.answer in model.OVER for a in actions)
        and real.raw.get("result", {}).get("verdict") == "approve"
    ]
    assert approved
    for actions in approved:
        builder = next(a for a in reversed(actions) if a.role in model.LATE)
        assert builder.answer is not model.Build.RED, [a.name for a in actions]
        assert actions[-1].answer is model.Review.APPROVES, [a.name for a in actions]


def test_on_the_real_workflow_every_dead_agent_ends_in_an_error_that_says_to_resume() -> None:
    dead = ending_on(lambda last: last.answer in model.FAILED)
    assert {last.role for last, _ in dead} == set(model.Role)
    for last, real in dead:
        assert "result" not in real.raw, last.name
        error = real.raw["error"]
        assert error["name"] == "Error", last.name
        assert error["message"].startswith(f"{last.fails_as} agent returned no result"), error
        assert world.RESUME in error["message"], error


@functools.cache
def replays() -> dict[str, world.Played]:
    named = {
        "dies": DIES_FIRST,
        "fixed over": FIXED_OVER_AND_APPROVED,
        "reconciled over": RECONCILED_OVER_AND_APPROVED,
        "measured over": MEASURED_OVER,
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


def test_a_change_a_fix_round_or_a_reconcile_takes_over_the_ceiling_is_approved() -> None:
    # Why: this is an agent's answer, a size over the ceiling with a green check, and not a
    # measured tree. On a real tree of an adopted project the size gate is one of the gates
    # `check` runs, so that builder and the review would report the check red.
    measured = world.BUILDS[model.Build.PRODUCT_OVER]
    assert isinstance(measured, dict)
    assert measured["changeSize"]["lines"] > measured["changeSize"]["ceiling"]
    assert measured["checkPasses"] is True
    for name in ("fixed over", "reconciled over"):
        result = replays()[name].raw["result"]
        assert (result["verdict"], result["forcedBy"], result["tooLarge"]) == ("approve", [], None)


def test_a_change_measured_over_a_ceiling_stops_with_no_parts_named() -> None:
    real = replays()["measured over"]
    assert real.calls == (("preflight", "Preflight"), ("spec", "Spec"))
    too = real.raw["result"]["tooLarge"]
    assert too == {"kind": "product", "stage": "the spec", "parts": 0}
