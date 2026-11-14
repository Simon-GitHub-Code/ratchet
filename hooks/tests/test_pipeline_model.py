"""One pipeline run's rules, on every run its model allows."""

from __future__ import annotations

import functools
from collections import deque

from tests import pipeline_model as model

(
    EVIDENCED,
    BOUNDED,
    ORDERED,
    STRICTER,
    CEILINGED,
    LOOP_ENDS,
    UNPUBLISHED,
    REPORTED,
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
    CEILINGED: RECONCILED_OVER,
    LOOP_ENDS: None,
    UNPUBLISHED: None,
    REPORTED: DIES_FIRST,
    JUDGED_AGAIN: None,
    SPLIT: MEASURED_OVER,
    ASSUMED: None,
    CRITIC_WAITS: None,
    CHECK_ASKS_NOTHING: None,
    NEVER_CRASHES: None,
    LOST: None,
}


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
    given = {(model.NAMED[name].role, model.NAMED[name].answer) for _, name, _ in graph()[1]}
    for role, answers in model.ANSWERS.items():
        for answer in answers:
            assert (role, answer) in given, (role, answer)
