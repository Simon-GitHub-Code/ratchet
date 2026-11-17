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
writes the module and its config there, named after the module, and the summary's too when the
model has one.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import importlib
import re
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


@dataclasses.dataclass(frozen=True)
class Invariant:
    says: str
    rule: str | Callable[[Any], bool]


@dataclasses.dataclass(frozen=True)
class Property:
    says: str
    rule: str


@dataclasses.dataclass(frozen=True)
class Step:
    says: str
    rule: Callable[[Any, Any, Any], bool]


Rule = Invariant | Property | Step


class Graph(Protocol):
    @property
    def states(self) -> Sequence[Any]: ...

    @property
    def edges(self) -> Sequence[tuple[Any, Any, Any]]: ...


class Export(NamedTuple):
    name: str
    module: str
    config: str


class Summary(NamedTuple):
    states: tuple[Any, ...]
    edges: tuple[tuple[Any, str, Any], ...]


def literal(value: object) -> str:
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
    return [
        f"{name}_ ==",
        "  <<",
        *(f"    {literal(v)}," for v in values[:-1]),
        f"    {literal(values[-1])}",
        "  >>",
    ]


def lowered(name: str) -> str:
    return name[0].lower() + name[1:]


@functools.cache
def identifier(words: str) -> str:
    return "".join(word[:1].upper() + word[1:] for word in re.findall(r"[A-Za-z0-9]+", words))


Node = tuple[Any, frozenset[str]]


def lifted(
    graph: Graph, steps: Mapping[str, Callable[[Any, Any, Any], bool]]
) -> tuple[list[Node], list[tuple[Node, Any, Node]]]:
    if not steps:
        return (
            [(state, frozenset()) for state in graph.states],
            [((a, frozenset()), act, (b, frozenset())) for a, act, b in graph.edges],
        )
    out: dict[Any, list[tuple[Any, Node]]] = {state: [] for state in graph.states}
    for source, act, target in graph.edges:
        broke = frozenset(name for name, keeps in steps.items() if not keeps(source, act, target))
        out[source].append((act, (target, broke)))
    start: Node = (graph.states[0], frozenset())
    seen = {start: None}
    edges: list[tuple[Node, Any, Node]] = []
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for act, after in out[node[0]]:
            edges.append((node, act, after))
            if after not in seen:
                seen[after] = None
                queue.append(after)
    return list(seen), edges


def export(  # noqa: PLR0913 # -- each keyword is one choice a model makes about its module
    module: str,
    graph: Graph,
    rules: Mapping[str, Rule],
    *,
    derived: Mapping[str, Callable[[Any], object]] | None = None,
    fields: Sequence[str] | None = None,
    action: Callable[[Any], str] = str,
    deadlock: bool = True,
) -> Export:
    steps = {name: rule.rule for name, rule in rules.items() if isinstance(rule, Step)}
    nodes, edges = lifted(graph, steps)
    number = {node: index for index, node in enumerate(nodes, 1)}
    first = nodes[0][0]
    names = list(fields) if fields is not None else [f.name for f in dataclasses.fields(first)]
    columns: dict[str, Callable[[Node], object]] = {
        **{name: (lambda node, name=name: getattr(node[0], name)) for name in names},
        **{name: (lambda node, f=f: f(node[0])) for name, f in (derived or {}).items()},
        **{
            lowered(name): (lambda node, f=rule.rule: f(node[0]))
            for name, rule in rules.items()
            if isinstance(rule, Invariant) and callable(rule.rule)
        },
        **{lowered(name): (lambda node, name=name: name not in node[1]) for name in steps},
    }
    by_action: dict[str, list[tuple[int, int]]] = {}
    for source, act, target in edges:
        by_action.setdefault(action(act), []).append((number[source], number[target]))
    variables = ["node", *columns]
    lines = [
        f"---- MODULE {module} ----",
        "EXTENDS Integers",
        f"VARIABLES {', '.join(variables)}",
        f"vars == <<{', '.join(variables)}>>",
        "",
    ]
    for name, column in columns.items():
        lines += [*table(name, [column(node) for node in nodes]), ""]
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
        config.append(f"{'PROPERTY' if isinstance(rule, Property) else 'INVARIANT'} {name}")
    lines.append("====")
    check = "TRUE" if deadlock else "FALSE"
    return Export(
        module, "\n".join(lines) + "\n", "\n".join([*config, f"CHECK_DEADLOCK {check}"]) + "\n"
    )


def summary(graph: Graph, of: Callable[[Any], Any], action: Callable[[Any], str] = str) -> Summary:
    states = dict.fromkeys(of(state) for state in graph.states)
    edges = dict.fromkeys((of(a), action(act), of(b)) for a, act, b in graph.edges)
    return Summary(tuple(states), tuple(edges))


def sha256(exported: Export) -> str:
    return hashlib.sha256(exported.module.encode()).hexdigest()


def stale(page: Path, *exports: Export) -> str:
    named = set(re.findall(r"SHA-256 `([0-9a-f]{64})`", page.read_text(encoding="utf-8")))
    now = {exported.name: sha256(exported) for exported in exports}
    if named == set(now.values()):
        return ""
    wanted = ", ".join(f"{name}.tla `{digest}`" for name, digest in now.items())
    return (
        f"the model changed since {page.name} was drawn: check and draw it again as"
        f" docs/model/README.md says, and name the new SHA-256 of {wanted}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="a module with an export() function, as tests.route_model")
    parser.add_argument("directory", type=Path)
    options = parser.parse_args(argv)
    model = importlib.import_module(options.model)
    options.directory.mkdir(parents=True, exist_ok=True)
    for exported in (model.export(), *([model.summary()] if hasattr(model, "summary") else [])):
        (options.directory / f"{exported.name}.tla").write_text(exported.module, encoding="utf-8")
        (options.directory / f"{exported.name}.cfg").write_text(exported.config, encoding="utf-8")
        print(f"{exported.name}: {options.directory / exported.name}.tla")  # noqa: T201 # -- the command reports
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
