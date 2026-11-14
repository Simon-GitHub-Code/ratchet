"""The TLA+ exporter on a toy machine: what each part of a model becomes in the module."""

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from tests import model_tla


@dataclass(frozen=True)
class Count:
    """The state of a toy machine: a number."""

    n: int


class Toy(NamedTuple):
    """A toy graph: two states and a step each way."""

    states: tuple[Count, ...] = (Count(1), Count(2))
    edges: tuple[tuple[Count, str, Count], ...] = (
        (Count(1), "Up", Count(2)),
        (Count(2), "Down", Count(1)),
    )


def test_a_field_is_a_table_read_at_the_node_the_machine_is_in() -> None:
    exported = model_tla.export("Toy", Toy(), {})
    assert "VARIABLES node, n\n" in exported.module
    assert "n_ ==\n  <<\n    1,\n    2\n  >>\n" in exported.module
    assert "Init == node = 1 /\\ n = n_[1]\n" in exported.module
    assert "Fill == n' = n_[node']\n" in exported.module


def test_an_action_is_the_pairs_of_states_its_edges_join() -> None:
    exported = model_tla.export("Toy", Toy(), {})
    assert "Up ==\n  /\\ \\/ (node = 1 /\\ node' = 2)\n  /\\ Fill\n" in exported.module
    assert "\nNext == Up \\/ Down\n" in exported.module


def test_a_rule_given_as_a_predicate_is_exported_as_a_derived_boolean() -> None:
    small = model_tla.Invariant("It stays small.", lambda count: count.n < 2)
    exported = model_tla.export("Toy", Toy(), {"Small": small})
    assert "VARIABLES node, n, small\n" in exported.module
    assert "small_ ==\n  <<\n    TRUE,\n    FALSE\n  >>\n" in exported.module
    assert "\\* It stays small.\nSmall ==\n  small\n" in exported.module
    assert exported.config == "SPECIFICATION Spec\nINVARIANT Small\nCHECK_DEADLOCK TRUE\n"


def test_a_rule_given_as_text_is_written_as_it_is_given() -> None:
    steps = model_tla.Property("It only climbs.", "[][n' > n]_vars")
    exported = model_tla.export("Toy", Toy(), {"Climbs": steps})
    assert "\\* It only climbs.\nClimbs ==\n  [][n' > n]_vars\n" in exported.module
    assert exported.config == "SPECIFICATION Spec\nPROPERTY Climbs\nCHECK_DEADLOCK TRUE\n"


def test_a_derived_value_is_a_variable_beside_the_fields() -> None:
    exported = model_tla.export("Toy", Toy(), {}, derived={"twice": lambda count: count.n * 2})
    assert "VARIABLES node, n, twice\n" in exported.module
    assert "twice_ ==\n  <<\n    2,\n    4\n  >>\n" in exported.module


class Stays(NamedTuple):
    """A toy graph with a step that changes nothing: from 1, staying or going up, then down."""

    states: tuple[Count, ...] = (Count(1), Count(2))
    edges: tuple[tuple[Count, str, Count], ...] = (
        (Count(1), "Stay", Count(1)),
        (Count(1), "Up", Count(2)),
        (Count(2), "Down", Count(1)),
    )


def test_a_step_rule_splits_each_state_by_the_rules_the_step_into_it_broke() -> None:
    # Why: the step that breaks the rule changes nothing, so only a state of its own shows it.
    moves = model_tla.Step("It never stays.", lambda _before, act, _after: act != "Stay")
    exported = model_tla.export("Toy", Stays(), {"Moves": moves})
    assert "VARIABLES node, n, moves\n" in exported.module
    assert "n_ ==\n  <<\n    1,\n    1,\n    2\n  >>\n" in exported.module
    assert "moves_ ==\n  <<\n    TRUE,\n    FALSE,\n    TRUE\n  >>\n" in exported.module
    assert "Stay ==\n  /\\ \\/ (node = 1 /\\ node' = 2)\n     \\/ (node = 2 /\\ node' = 2)\n" in (
        exported.module
    )
    assert "Down ==\n  /\\ \\/ (node = 3 /\\ node' = 1)\n  /\\ Fill\n" in exported.module
    assert "\\* It never stays.\nMoves ==\n  moves\n" in exported.module
    assert exported.config == "SPECIFICATION Spec\nINVARIANT Moves\nCHECK_DEADLOCK TRUE\n"


def test_a_step_rule_that_holds_leaves_the_graph_as_it_is() -> None:
    kept = model_tla.Step("It moves or stays.", lambda _before, _act, _after: True)
    exported = model_tla.export("Toy", Stays(), {"Kept": kept})
    assert "n_ ==\n  <<\n    1,\n    2\n  >>\n" in exported.module


def test_an_action_is_named_by_the_function_given_and_a_bound_is_no_deadlock() -> None:
    exported = model_tla.export("Toy", Toy(), {}, action=str.upper, deadlock=False)
    assert "\nNext == UP \\/ DOWN\n" in exported.module
    assert exported.config == "SPECIFICATION Spec\nCHECK_DEADLOCK FALSE\n"


def test_a_summary_has_a_state_for_each_value_and_an_edge_for_each_action_between_two() -> None:
    parity = model_tla.summary(Stays(), lambda count: count.n % 2 == 0)
    assert parity.states == (False, True)
    assert parity.edges == ((False, "Stay", False), (False, "Up", True), (True, "Down", False))


def test_an_identifier_is_the_words_capitalised() -> None:
    assert model_tla.identifier("another branch lands: bind the event's row") == (
        "AnotherBranchLandsBindTheEventSRow"
    )


def test_a_page_is_stale_until_it_names_the_sha_256_of_each_export_and_no_other(
    tmp_path: Path,
) -> None:
    up, down = model_tla.export("Up", Toy(), {}), model_tla.export("Down", Stays(), {})
    page = tmp_path / "page.md"
    page.write_text(f"Checked on SHA-256 `{model_tla.sha256(up)}`.\n", encoding="utf-8")
    assert not model_tla.stale(page, up)
    assert model_tla.sha256(down) in model_tla.stale(page, up, down)
    assert model_tla.stale(page, down)
