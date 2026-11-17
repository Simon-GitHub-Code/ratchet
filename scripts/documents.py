from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

CATALOGUE = Path(__file__).resolve().parents[1] / "skills/method/application-types.toml"
SECTION = "## Documents"
COLUMNS = ("Kind", "Paths", "Prune")
MARKDOWN = ".md"
STANDING = "standing"
STEERING = "steering"
NEVER = "never"
CUT = "pass"
PRUNE = re.compile(rf"`?({NEVER}|{CUT})`?(?:\s+keeping\s+(.+))?")


@dataclass(frozen=True)
class Kind:
    name: str
    paths: tuple[str, ...]
    lifetime: str
    prune: str
    keeps: tuple[str, ...] = ()

    @property
    def steering(self) -> bool:
        return self.lifetime == STEERING

    def holds(self, path: str) -> bool:
        return gatelib.matches(path, list(self.paths))


@dataclass(frozen=True)
class Declared:
    kinds: tuple[Kind, ...]
    problems: tuple[str, ...]


def shipped(text: str | None = None) -> tuple[Kind, ...]:
    data = tomllib.loads(text if text is not None else CATALOGUE.read_text(encoding="utf-8"))
    return tuple(
        Kind(
            row["name"],
            tuple(row["paths"]),
            row["lifetime"],
            row["prune"],
            tuple(row.get("keeps", ())),
        )
        for row in data.get("documents", ())
    )


def declared(binding: str | None) -> Declared | None:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None
    tables = [t for t in markdown_text.tables(binding, SECTION) if t.header == COLUMNS]
    if not tables:
        columns = " | ".join(COLUMNS)
        return Declared((), (f"the section lacks its table with the columns {columns}",))
    kinds: list[Kind] = []
    problems: list[str] = []
    for row in tables[0].rows:
        kind, problem = row_kind(row, {held.name for held in kinds})
        if problem:
            problems.append(problem)
        if kind:
            kinds.append(kind)
    return Declared(tuple(kinds), tuple(problems))


def row_kind(row: Sequence[str], held: set[str]) -> tuple[Kind | None, str | None]:
    name = row[0].strip("` ")
    if name.startswith("<") or row[1].startswith("<") or row[2].startswith("<"):
        return None, "the table still has the template's placeholder row"
    if name in held:
        return None, f"the kind '{name}' is listed twice"
    globs = tuple(glob.strip() for item in markdown_text.ticked(row[1]) for glob in item.split(","))
    if not globs:
        return None, f"'{name}' names no paths: write the globs in backticks"
    prune = PRUNE.fullmatch(row[2].strip())
    if prune is None:
        return None, f"'{name}' has a Prune of neither `{NEVER}` nor `{CUT}`"
    keeps = tuple(markdown_text.ticked(prune.group(2) or ""))
    if keeps and prune.group(1) == NEVER:
        return None, f"'{name}' is `{NEVER}` and keeps headings: only a `{CUT}` row keeps any"
    return Kind(name, globs, STANDING, prune.group(1), keeps), None


def all_kinds(binding: str | None, catalogue: str | None = None) -> tuple[Kind, ...]:
    own = declared(binding)
    return (*shipped(catalogue), *(own.kinds if own else ()))


def kind_of(path: str, kinds: Iterable[Kind]) -> Kind | None:
    return next((kind for kind in kinds if kind.holds(path)), None)


def unkinded(paths: Iterable[str], kinds: Sequence[Kind]) -> list[str]:
    return sorted(
        path for path in paths if path.endswith(MARKDOWN) and kind_of(path, kinds) is None
    )


def kinds_of(root: Path) -> tuple[Kind, ...]:
    return all_kinds(gatelib.read_text(root / gatelib.BINDING))
