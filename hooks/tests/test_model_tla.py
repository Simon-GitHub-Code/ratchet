"""The TLA+ exporter on a toy machine: what each part of a model becomes in the module."""

from dataclasses import dataclass
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
