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
    LOOP_ENDS,
    UNPUBLISHED,
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
    CEILINGED: None,
    MEASURED: None,
    LOOP_ENDS: None,
    UNPUBLISHED: None,
    # 8 breaks on a dead agent, because the script raises and returns no result. 8a holds. It is
    # ADR 0023 decision 2: "One run per ticket also keeps its own run to resume, so a ticket that
    # dies is run again alone." The script's error says to resume the run.
    REPORTED: DIES_FIRST,
    RESUMABLE: None,
    JUDGED_AGAIN: None,
    # 10 breaks on a change a reconcile, a fix round, the implementer or the spec measures over a
    # ceiling, none of which names parts (the shortest is a reconcile's). 10a holds.
    # pipeline.md step 3 says the stop on the spec's estimate comes "with a proposed split" and
    # says it of no other stop, and pipeline.js tells the orchestrator "with no `proposedSplit`,
    # divide the ticket's promises yourself".
    SPLIT: RECONCILED_OVER,
    SPLIT_UNASKED: None,
    ASSUMED: None,
    CRITIC_WAITS: None,
    CHECK_ASKS_NOTHING: None,
    NEVER_CRASHES: None,
    LOST: None,
}
# Why: a fix round and a reconcile over a ceiling, which the real workflow stops as too large.
RECONCILED_OVER = [
    "the orchestrator judges a branch whose rebase conflicted again",
    "reconcile measures product code over its ceiling",
]
FIXED_OVER = [
    "the orchestrator starts a build",
    "preflight is green for a project that is depended-on",
    "spec fits",
    "ticket-check finds no gap",
    "implement is green",
    "critique finds a major it does not see fixed",
    "fix-1 measures product code over its ceiling",
]


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


def test_on_the_real_workflow_every_dead_agent_ends_in_an_error_that_says_to_resume() -> None:
    dead = ending_on(lambda last: last.answer in model.FAILED)
    assert {last.role for last, _ in dead} == set(model.Role)
    for last, real in dead:
        assert "result" not in real.raw, last.name
        error = real.raw["error"]
        assert error["name"] == "Error", last.name
        assert error["message"].startswith(f"{last.fails_as} agent returned no result"), error
        assert world.RESUME in error["message"], error


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
        "reconciled over": RECONCILED_OVER,
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


def test_a_change_a_fix_round_or_a_reconcile_takes_over_the_ceiling_stops_as_too_large() -> None:
    # Why: ADR 0055. The size was once read after the spec and the implementation only, and
    # these two ended in an approval on an agent's green answer.
    for name, stage, calls in (
        ("fixed over", "fix round 1", ("fix-1", "Fix")),
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
