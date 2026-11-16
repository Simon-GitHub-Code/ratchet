"""What a change to a justfile could affect, read from ``just --dump --dump-format json``.

Two texts of a justfile, at the merge base and at head, are written to scratch files and dumped
with ``--justfile``, so nothing in them runs. A changed recipe affects each gate whose command
reaches it, through its dependencies and through ``just <recipe>`` calls in recipe bodies, and
transitively. A change anywhere else in the justfile (an assignment, a ``set`` line, an import, a
module), or a dump that fails, affects every gate: the ``Cause`` has no gate then.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import justfile_text

if TYPE_CHECKING:
    from collections.abc import Mapping

DUMP_TIMEOUT = 30
OUTSIDE_RECIPES = frozenset({"recipes", "source", "first", "warnings"})


class Cause(NamedTuple):
    """Why a gate is in the proof's set; no gate means every gate of tiers 0 and 1."""

    gate: str | None
    why: str


def dump(text: str, file: Path) -> dict[str, object] | None:
    """The justfile ``text`` as ``just`` parses it, or ``None`` when ``just`` cannot read it."""
    file.write_text(text, encoding="utf-8")
    command = ["just", "--justfile", str(file), "--dump", "--dump-format", "json"]
    try:
        done = subprocess.run(  # -- a fixed argv, no shell
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


def recipes_of(data: Mapping[str, object]) -> dict[str, dict[str, object]]:
    table = data.get("recipes")
    if not isinstance(table, dict):
        return {}
    return {name: recipe for name, recipe in table.items() if isinstance(recipe, dict)}


def called(recipe: Mapping[str, object]) -> set[str]:
    """The recipes one recipe names: its dependencies and the ``just x`` calls in its body."""
    names: set[str] = set()
    dependencies = recipe.get("dependencies")
    for dependency in dependencies if isinstance(dependencies, list) else []:
        if isinstance(dependency, dict):
            names.add(str(dependency.get("recipe")))
    body = recipe.get("body")
    for line in body if isinstance(body, list) else []:
        words = (
            "".join(part for part in line if isinstance(part, str))
            if isinstance(line, list)
            else ""
        )
        names.update(justfile_text.JUST_CALL.findall(words))
    return names


def reached(recipes: Mapping[str, Mapping[str, object]], command: str) -> set[str]:
    """Every recipe a gate's command runs, directly or through what a recipe runs."""
    pending = list(justfile_text.JUST_CALL.findall(command))
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in recipes:
            continue
        seen.add(name)
        pending.extend(called(recipes[name]))
    return seen


def causes(
    path: str, old: str | None, new: str | None, *, commands: Mapping[str, str]
) -> list[Cause]:
    """The gates a change from ``old`` to ``new`` could affect; ``commands``: gate to command."""
    if old is None or new is None:
        return [Cause(None, f"{path} was added or removed, and no record says which gate runs it")]
    if justfile_text.imports(old) != justfile_text.imports(new) or justfile_text.modules(
        old
    ) != justfile_text.modules(new):
        return [Cause(None, f"{path} changed an import or a module")]
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
    old_recipes, new_recipes = recipes_of(before), recipes_of(after)
    changed = sorted(
        name
        for name in old_recipes.keys() | new_recipes.keys()
        if old_recipes.get(name) != new_recipes.get(name)
    )
    found: list[Cause] = []
    for gate, command in sorted(commands.items()):
        reach = reached(old_recipes, command) | reached(new_recipes, command)
        found += [
            Cause(gate, f"recipe `{name}` changed, and the gate's command reaches it")
            for name in changed
            if name in reach
        ]
    return found
