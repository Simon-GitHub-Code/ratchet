"""The queue drain's rules, on every state of its model."""

from __future__ import annotations

import functools

import pytest

from tests import drain_model as model
from tests.drain_model import Kind

CAP, KEPT, JUDGED, NOT_STALE, ONCE, HELD, REFUSED, NO_TREE = model.RULES

OPEN_A = ["drain.py open a", "the agent commits a"]
JUDGED_A = [*OPEN_A, "the agent commits the verdict record of a"]
PARK_A = "drain.py close --outcome parked a"
# Why: a rule the code does not keep is pinned here by the shortest path from the initial state
# to a step that breaks it, so the break is a recorded fact and not a red test.
COUNTEREXAMPLES: dict[str, list[str] | None] = {
    CAP: [*OPEN_A, PARK_A, "drain.py open c", "the owner edits ticket a", "drain.py update a"],
    KEPT: None,
    JUDGED: None,
    NOT_STALE: None,
    ONCE: [*JUDGED_A, "drain.py land a", PARK_A],
    HELD: [*JUDGED_A, PARK_A, "drain.py land a"],
    REFUSED: None,
    NO_TREE: [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        "git leaves a stale index lock in the tree of c",
        "drain.py clean --all",
    ],
}


@functools.cache
def search() -> tuple[dict[str, list[str]], frozenset[str]]:
    """Each rule's shortest break, read off the graph's breadth-first steps; every action taken."""
    _, edges = model.graph()
    came: dict[model.State, tuple[model.State, str] | None] = {model.INITIAL: None}
    breaks: dict[str, list[str]] = {}
    for before, said, after in edges:
        for name, rule_holds in model.RULES.items():
            if name not in breaks and not rule_holds(before, said, after):
                path, at = [said], came[before]
                while at is not None:
                    path.append(at[1])
                    at = came[at[0]]
                breaks[name] = path[::-1]
        came.setdefault(after, (before, said))
    return breaks, frozenset(said for _, said, _ in edges)


@pytest.mark.parametrize("name", list(model.RULES))
def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path(
    name: str,
) -> None:
    found = search()[0].get(name)
    assert found == COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(found or [])


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


def test_every_kind_of_action_is_reachable() -> None:
    _, taken = search()
    for kind in Kind:
        assert any(name.startswith(kind.value) for name in taken), kind
