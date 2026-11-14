"""A model's reachable graph as a TLA+ module, for a model checker and a statechart.

Each model in the tests is pure: frozen states, actions that yield the states they lead to, and a
breadth-first ``graph()`` of everything reachable. ``export`` writes that graph as a module TLC can
walk, with the model's rules as invariants and properties, so a checker can confirm the rules the
tests prove and the statechart tool can draw the machine. Nothing here runs TLC or needs Java.

The module numbers the states in the order the graph lists them. ``node`` is the number of the
state the machine is in; every field, and every derived variable, is a table read at ``node``. An
action is the disjunction of its edges, each a pair of numbers, so the module stays small however
many fields a state has, and two runs write the same bytes.

Usage, from the ``hooks`` directory: ``python3 -m tests.model_tla tests.route_model DIRECTORY``
writes the module and its config there, named after the module.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


@dataclasses.dataclass(frozen=True)
class Invariant:
    """A rule every reachable state keeps: TLA+ text, or a predicate over a state.

    A predicate becomes a derived boolean, named after the rule with a lower-case first letter,
    and the invariant is that boolean.
    """

    says: str
    rule: str | Callable[[Any], bool]


@dataclasses.dataclass(frozen=True)
class Property:
    """A rule over steps, as TLA+ text: ``[][...]_vars``."""

    says: str
    rule: str


Rule = Invariant | Property


class Graph(Protocol):
    """What ``export`` reads of a model's graph: its states, the first the start, and its edges."""

    @property
    def states(self) -> Sequence[Any]: ...

    @property
    def edges(self) -> Sequence[tuple[Any, Any, Any]]: ...


class Export(NamedTuple):
    """The two files a model checker reads."""

    name: str
    module: str
    config: str


def literal(value: object) -> str:
    """A TLA+ value: a boolean, an integer, a string, a tuple or a set of them."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, tuple):
        return "<<" + ", ".join(literal(item) for item in value) + ">>"
    if isinstance(value, frozenset):
        return "{" + ", ".join(sorted(literal(item) for item in value)) + "}"
    return f'"{value}"'


def table(name: str, values: list[object]) -> list[str]:
    """``name_ == <<...>>``, one value per state, one value to a line."""
    return [
        f"{name}_ ==",
        "  <<",
        *(f"    {literal(v)}," for v in values[:-1]),
        f"    {literal(values[-1])}",
        "  >>",
    ]


def lowered(name: str) -> str:
    return name[0].lower() + name[1:]


def export(
    module: str,
    graph: Graph,
    rules: Mapping[str, Rule],
    *,
    derived: Mapping[str, Callable[[Any], object]] | None = None,
    fields: Sequence[str] | None = None,
) -> Export:
    """The module and config for ``graph``, checking ``rules``.

    ``fields`` are the variables read off each state, by default its dataclass fields;
    ``derived`` adds a variable per entry, a function of the state. The actions are named by the
    edges, in the order they first appear.
    """
    states = list(graph.states)
    number = {state: index for index, state in enumerate(states, 1)}
    names = list(fields or [field.name for field in dataclasses.fields(states[0])])
    columns: dict[str, Callable[[Any], object]] = {
        **{name: (lambda state, name=name: getattr(state, name)) for name in names},
        **(derived or {}),
        **{
            lowered(name): rule.rule
            for name, rule in rules.items()
            if isinstance(rule, Invariant) and callable(rule.rule)
        },
    }
    by_action: dict[str, list[tuple[int, int]]] = {}
    for source, act, target in graph.edges:
        by_action.setdefault(str(act), []).append((number[source], number[target]))
    variables = ["node", *columns]
    lines = [
        f"---- MODULE {module} ----",
        "EXTENDS Integers",
        f"VARIABLES {', '.join(variables)}",
        f"vars == <<{', '.join(variables)}>>",
        "",
    ]
    for name, column in columns.items():
        lines += [*table(name, [column(state) for state in states]), ""]
    lines += [
        "Init == node = 1 /\\ " + " /\\ ".join(f"{name} = {name}_[1]" for name in columns),
        "Fill == " + " /\\ ".join(f"{name}' = {name}_[node']" for name in columns),
        "",
    ]
    for act, pairs in by_action.items():
        lines.append(f"{act} ==")
        lines.append(
            "  /\\ \\/ " + "\n     \\/ ".join(f"(node = {a} /\\ node' = {b})" for a, b in pairs)
        )
        lines += ["  /\\ Fill", ""]
    lines += ["Next == " + " \\/ ".join(by_action), "Spec == Init /\\ [][Next]_vars", ""]
    config = ["SPECIFICATION Spec"]
    for name, rule in rules.items():
        text = lowered(name) if callable(rule.rule) else rule.rule
        lines += [
            f"\\* {rule.says}",
            f"{name} ==",
            *(f"  {line}" for line in text.splitlines()),
            "",
        ]
        config.append(f"{'INVARIANT' if isinstance(rule, Invariant) else 'PROPERTY'} {name}")
    lines.append("====")
    return Export(
        module, "\n".join(lines) + "\n", "\n".join([*config, "CHECK_DEADLOCK TRUE"]) + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Write the module and config of the model module named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="a module with an export() function, as tests.route_model")
    parser.add_argument("directory", type=Path)
    options = parser.parse_args(argv)
    exported: Export = importlib.import_module(options.model).export()
    options.directory.mkdir(parents=True, exist_ok=True)
    (options.directory / f"{exported.name}.tla").write_text(exported.module, encoding="utf-8")
    (options.directory / f"{exported.name}.cfg").write_text(exported.config, encoding="utf-8")
    print(f"{exported.name}: {options.directory / exported.name}.tla")  # noqa: T201 # -- the command reports
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
