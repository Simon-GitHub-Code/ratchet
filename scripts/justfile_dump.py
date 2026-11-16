"""What a change to a justfile could affect, read from ``just --dump --dump-format json``.

Two texts of a justfile, at the merge base and at head, are written to scratch files and dumped
with ``--justfile``, so nothing in them runs. A changed recipe affects each gate whose command
reaches it. A command reaches a recipe by naming it or an alias of it, and a recipe reaches the
recipes it depends on and every recipe a word of its body names, so ``just x``, a runner held in
a variable and ``{{just_executable()}} x`` all count. A body that runs ``just`` with a name it
cannot read reaches every recipe. A justfile with any ``import`` or ``mod`` line, a change
anywhere else in it (an assignment, a ``set`` line), or a dump that fails, affects every gate: the
``Cause`` has no gate then.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import justfile_text

if TYPE_CHECKING:
    from collections.abc import Mapping

DUMP_TIMEOUT = 30
OUTSIDE_RECIPES = frozenset({"recipes", "source", "first", "warnings"})
NAME = re.compile(r"[A-Za-z_][\w-]*")
RUNNER = re.compile(r"(?<![\w-])just(?![\w-])")
JUST_WORD = re.compile(r"\bjust\b")


class Cause(NamedTuple):
    """Why a gate is in the proof's set; no gate means every gate of tiers 0 and 1."""

    gate: str | None
    why: str


class Graph(NamedTuple):
    """The recipes of one dump, the aliases that name them, and the variables that hold ``just``."""

    recipes: dict[str, dict[str, object]]
    aliases: dict[str, str]
    runners: frozenset[str]


def dump(text: str, file: Path) -> dict[str, object] | None:
    """The justfile ``text`` as ``just`` parses it, or ``None`` when ``just`` cannot read it."""
    file.write_text(text, encoding="utf-8")
    command = ["just", "--justfile", str(file), "--dump", "--dump-format", "json"]
    try:
        done = subprocess.run(
            command,
            cwd=file.parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=DUMP_TIMEOUT,
        )
        data = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return data if isinstance(data, dict) else None


def table(data: Mapping[str, object], key: str) -> dict[str, dict[str, object]]:
    found = data.get(key)
    if not isinstance(found, dict):
        return {}
    return {name: value for name, value in found.items() if isinstance(value, dict)}


def graph_of(data: Mapping[str, object]) -> Graph:
    aliases = {name: str(alias.get("target")) for name, alias in table(data, "aliases").items()}
    runners = frozenset(
        name
        for name, assignment in table(data, "assignments").items()
        if JUST_WORD.search(str(assignment.get("value")))
    )
    return Graph(table(data, "recipes"), aliases, runners)


def items_of(line: object) -> list[str | list[object]]:
    """A body line as its words and its interpolations, in order."""
    parts = line if isinstance(line, list) else []
    items: list[str | list[object]] = []
    for part in parts:
        if isinstance(part, str):
            items += part.split()
        elif isinstance(part, list):
            items.append(part)
    return items


def runs_just(item: str | list[object], graph: Graph) -> bool:
    if isinstance(item, str):
        return RUNNER.search(item) is not None
    shown = json.dumps(item)
    return "just_executable" in shown or any(f'["variable", "{v}"]' in shown for v in graph.runners)


def unreadable(items: list[str | list[object]], graph: Graph) -> bool:
    """Whether a line runs ``just`` with something after it that is no recipe name."""
    for at, item in enumerate(items):
        if not runs_just(item, graph):
            continue
        rest = [
            other for other in items[at + 1 :] if not (isinstance(other, str) and other[:1] == "-")
        ]
        if rest and not (isinstance(rest[0], str) and NAME.fullmatch(rest[0])):
            return True
    return False


def named(words: str, graph: Graph) -> set[str]:
    """The recipes a text names by their own name or an alias's."""
    found = set(NAME.findall(words))
    return {
        graph.aliases.get(name, name)
        for name in found
        if name in graph.recipes or name in graph.aliases
    }


def called(recipe: Mapping[str, object], graph: Graph) -> set[str] | None:
    """The recipes one recipe reaches by itself; ``None`` when it may reach every recipe."""
    dependencies = recipe.get("dependencies")
    names = {
        str(dependency.get("recipe"))
        for dependency in (dependencies if isinstance(dependencies, list) else [])
        if isinstance(dependency, dict)
    }
    body = recipe.get("body")
    for line in body if isinstance(body, list) else []:
        items = items_of(line)
        if unreadable(items, graph):
            return None
        names |= named(" ".join(item for item in items if isinstance(item, str)), graph)
    return names


def reached(graph: Graph, command: str) -> set[str]:
    """Every recipe a gate's command runs, directly or through what a recipe runs."""
    if unreadable(items_of([command]), graph):
        return set(graph.recipes)
    pending = list(named(command, graph))
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in graph.recipes:
            continue
        seen.add(name)
        more = called(graph.recipes[name], graph)
        if more is None:
            return set(graph.recipes)
        pending.extend(more)
    return seen


def causes(
    path: str, old: str | None, new: str | None, *, commands: Mapping[str, str]
) -> list[Cause]:
    """The gates a change from ``old`` to ``new`` could affect; ``commands``: gate to command."""
    if old is None or new is None:
        return [Cause(None, f"{path} was added or removed, and no record says which gate runs it")]
    if any(justfile_text.imports(text) or justfile_text.modules(text) for text in (old, new)):
        return [Cause(None, f"{path} has an import or a module, which the dump does not follow")]
    with tempfile.TemporaryDirectory(prefix="ratchet-bite-just-") as scratch:
        before = dump(old, Path(scratch) / "base")
        after = dump(new, Path(scratch) / "head")
    if before is None or after is None:
        return [Cause(None, f"just could not read {path}, so nothing says which gate it affects")]
    outside = [
        {key: value for key, value in data.items() if key not in OUTSIDE_RECIPES}
        for data in (before, after)
    ]
    if outside[0] != outside[1]:
        return [Cause(None, f"{path} changed an assignment or a setting, which every recipe reads")]
    old_graph, new_graph = graph_of(before), graph_of(after)
    changed = sorted(
        name
        for name in old_graph.recipes.keys() | new_graph.recipes.keys()
        if old_graph.recipes.get(name) != new_graph.recipes.get(name)
    )
    found: list[Cause] = []
    for gate, command in sorted(commands.items()):
        reach = reached(old_graph, command) | reached(new_graph, command)
        found += [
            Cause(gate, f"recipe `{name}` changed, and the gate's command reaches it")
            for name in changed
            if name in reach
        ]
    return found
