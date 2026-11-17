from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import justfile_text
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

SECTION = "## Install"
INSTALL = "install"
RECIPE = "just install"
NONE_PREFIX = "none:"
MIN_REASON = 10
LABELS = ("Install", "Lockfile", "Needed by")
WORD = re.compile(r"[\w./@+-]+")
NO_OPS = frozenset({"", "true", ":"})


@dataclass(frozen=True)
class Spec:
    needed: bool
    lockfile: str
    recipes: tuple[str, ...]
    problems: tuple[str, ...]


def _placeholder(value: str) -> bool:
    return value.startswith("<") or not value


def _refused(problem: str) -> Spec:
    return Spec(needed=False, lockfile="", recipes=(), problems=(problem,))


def parse(binding: str | None) -> Spec | None:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None
    fields = markdown_text.bullets(markdown_text.section(binding, SECTION))
    install = fields.get(LABELS[0], "")
    plain = install.strip("` ")
    if _placeholder(plain):
        return _refused(
            f"'- {LABELS[0]}:' is still the template's: say `{RECIPE}` or `none: <reason>`"
        )
    if plain.lower().startswith(NONE_PREFIX):
        if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            return _refused("'- Install: none:' needs the reason no install comes first")
        return Spec(needed=False, lockfile="", recipes=(), problems=())
    if plain != RECIPE:
        return _refused(f"'- Install:' is `{RECIPE}` or `none: <reason>`, not '{install}'")
    problems: list[str] = []
    locked = markdown_text.ticked(fields.get(LABELS[1], ""))
    needed = tuple(markdown_text.ticked(fields.get(LABELS[2], "")))
    if len(locked) != 1:
        problems.append("'- Lockfile:' names the one file the install is frozen to, in backticks")
    if not needed:
        problems.append("'- Needed by:' names the recipes that run a tool the install provides")
    return Spec(
        needed=True,
        lockfile=locked[0] if locked else "",
        recipes=needed,
        problems=tuple(problems),
    )


def _body(justfile: str) -> str:
    lines = justfile_text.definition(justfile, INSTALL)
    header = next((i for i, line in enumerate(lines) if line.startswith(INSTALL)), len(lines))
    return justfile_text.code_of("\n".join(lines[header + 1 :]))


def installs_from_lock(justfile: str, lockfile: str) -> bool:
    return lockfile in WORD.findall(_body(justfile))


def reaches_install(justfile: str, recipe: str) -> bool:
    return INSTALL in justfile_text.reaches(justfile, recipe)


def depends_directly(justfile: str, recipe: str) -> bool:
    block = next((b for b in justfile_text.blocks(justfile.split("\n")) if b.name == recipe), None)
    return block is not None and block.parsed is not None and INSTALL in block.parsed.before


def derived(
    justfile: str, *, tracked: Callable[[str], bool], tiers: Collection[str]
) -> dict[str, str] | None:
    body = _body(justfile)
    lines = [line.strip().lstrip("@").strip() for line in body.splitlines()]
    waiting = [
        block.name
        for block in justfile_text.blocks(justfile.split("\n"))
        if block.parsed is not None
        and block.name not in {INSTALL, *tiers}
        and INSTALL in block.parsed.before
    ]
    if justfile_text.defines_recipe(justfile, INSTALL) and set(lines) <= NO_OPS:
        if waiting:
            return None
        return {
            LABELS[0]: f"`{NONE_PREFIX} the justfile's install recipe does nothing, and no recipe"
            " waits on it`"
        }
    lockfile = next((word for word in WORD.findall(body) if tracked(word)), None)
    if lockfile is None or not waiting:
        return None
    return {
        LABELS[0]: f"`{RECIPE}`",
        LABELS[1]: f"`{lockfile}`",
        LABELS[2]: ", ".join(f"`{name}`" for name in waiting),
    }
