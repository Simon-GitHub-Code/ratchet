"""JavaScript tools in an adopted justfile: which recipes need an install first (ADR 0039).

A package manager that does not install on demand (npm, pnpm, yarn, bun) leaves its tools in
``node_modules`` only after an install. ``uv run --frozen`` syncs ``.venv`` as it runs, so a Python
recipe works in a fresh checkout; ``npx --no-install biome`` does not, and ``merge.py``'s work tree
and every CI job are fresh checkouts. The rule: a recipe that runs a tool from such a manager
depends on an ``install`` recipe that installs from the lockfile and is cheap once it has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import justfile_text

if TYPE_CHECKING:
    from pathlib import Path

    from gatelib import Want

INSTALL = "install"
# Why: the stamp lives in the directory it vouches for, so deleting node_modules reinstalls.
STAMP = "node_modules/.ratchet-install"

# Why: a recipe that runs a tool through the package manager, or the binary the install links.
# `npm ci`, `npm audit`, `yarn add` and the like run no tool, and `pnpm dlx` fetches its own.
TOOL_RUN = re.compile(
    r"\bnpx\b"
    r"|\bnpm\s+(?:run|run-script|exec|x|test|start)\b"
    r"|\bpnpm\s+(?:exec|run|test|start)\b"
    r"|\byarn\s+(?!(?:install|add|remove|audit|outdated|info|why|config|set|cache|dedupe|up"
    r"|upgrade|version|init|npm|dlx)\b)[\w@./:-]"
    r"|\bbunx\b"
    r"|\bbun\s+(?:run|test|x)\b"
    r"|node_modules/\.bin/"
)
NAME = re.compile(r"[A-Za-z_][\w-]*")
EXPANSIONS = 4


@dataclass(frozen=True)
class Manager:
    """A package manager that installs ahead: its lockfile, its frozen install, how to tell it."""

    name: str
    lockfile: str
    command: str
    frozen: re.Pattern[str]

    @property
    def frozen_name(self) -> str:
        """The install without its quiet flags: ``npm ci``, ``yarn install --immutable``."""
        return self.command.removesuffix(" --no-audit --no-fund")


NPM = Manager(
    "npm",
    "package-lock.json",
    "npm ci --no-audit --no-fund",
    re.compile(r"\bnpm\s+ci\b"),
)
PNPM = Manager(
    "pnpm",
    "pnpm-lock.yaml",
    "pnpm install --frozen-lockfile",
    re.compile(r"\bpnpm\s+(?:install|i)\b[^\n]*--frozen-lockfile"),
)
YARN_CLASSIC = Manager(
    "yarn",
    "yarn.lock",
    "yarn install --frozen-lockfile",
    re.compile(r"\byarn\s+install\b[^\n]*--(?:immutable|frozen-lockfile)"),
)
YARN_BERRY = Manager("yarn", "yarn.lock", "yarn install --immutable", YARN_CLASSIC.frozen)
BUN = Manager(
    "bun",
    "bun.lock",
    "bun install --frozen-lockfile",
    re.compile(r"\bbun\s+install\b[^\n]*--frozen-lockfile"),
)
BUN_BINARY = Manager("bun", "bun.lockb", BUN.command, BUN.frozen)
BERRY_MARK = "__metadata:"


def manager_of(root: Path) -> Manager | None:
    """The package manager the root's lockfile says the project's tools come from, if any."""
    for found in (NPM, PNPM, BUN, BUN_BINARY):
        if (root / found.lockfile).is_file():
            return found
    shrinkwrap = root / "npm-shrinkwrap.json"
    if shrinkwrap.is_file():
        return Manager("npm", shrinkwrap.name, NPM.command, NPM.frozen)
    lock = root / YARN_CLASSIC.lockfile
    if lock.is_file():
        berry = BERRY_MARK in lock.read_text(encoding="utf-8", errors="replace")
        return YARN_BERRY if berry else YARN_CLASSIC
    return None


def expanded(justfile: str, body: str) -> str:
    """The body with each bare variable it interpolates replaced by its value, a few levels deep."""
    variables = justfile_text.variables(justfile)

    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        return variables.get(name, match.group(0)) if NAME.fullmatch(name) else match.group(0)

    for _ in range(EXPANSIONS):
        body = justfile_text.INTERPOLATION.sub(value, body)
    return body


def tool_recipes(justfile: str) -> list[str]:
    """The recipes, besides ``install``, whose lines run a tool the lockfile's manager installs."""
    lines = justfile.split("\n")
    return [
        block.name
        for block in justfile_text.blocks(lines)
        if block.parsed is not None
        and block.name != INSTALL
        and TOOL_RUN.search(
            expanded(justfile, justfile_text.code_of("\n".join(lines[block.head + 1 : block.end])))
        )
    ]


def installs_from_lock(justfile: str, manager: Manager) -> bool:
    """Whether the justfile's ``install`` recipe runs the manager's frozen install."""
    body = justfile_text.definition(justfile, INSTALL)
    return bool(body) and manager.frozen.search(justfile_text.code_of("\n".join(body))) is not None


def reaches_install(justfile: str, recipe: str) -> bool:
    """Whether running ``recipe`` runs the ``install`` recipe first or on the way."""
    return INSTALL in justfile_text.reaches(justfile, recipe)


def depends_directly(justfile: str, recipe: str) -> bool:
    """Whether ``recipe`` names ``install`` among its own dependencies."""
    block = next((b for b in justfile_text.blocks(justfile.split("\n")) if b.name == recipe), None)
    return block is not None and block.parsed is not None and INSTALL in block.parsed.before


def recipe_lines(manager: Manager) -> list[str]:
    """The ``install`` recipe for a project locked by ``manager``, comment above, as lines."""
    lock = manager.lockfile
    return [
        "# Install the tools from the lockfile, once: a recipe that runs one depends on this, so a",
        "# fresh checkout (the merge's work tree, a CI job) has them as a used one does. The tiers",
        "# run it before the gates start, so no two gates install at once (ADR 0039).",
        f"{INSTALL}:",
        (
            f"    @cmp -s {lock} {STAMP} 2>/dev/null || {{ {manager.command}"
            f" && mkdir -p node_modules && cp {lock} {STAMP}; }}"
        ),
    ]


def with_install(manager: Manager) -> Want:
    """The want of a justfile whose ``install`` recipe installs from ``manager``'s lockfile.

    A recipe of that name that does something else is replaced, one that already does is kept, and
    none is added before the first tier, or at the end when there is no tier.
    """

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        present = justfile_text.blocks(lines)
        if installs_from_lock(text, manager):
            return text
        made = recipe_lines(manager)
        old = next((b for b in present if b.name == INSTALL), None)
        if old is not None:
            lines[old.start : old.end] = made
            return "\n".join(lines)
        tier = next((b for b in present if b.name == "tier0"), None)
        if tier is not None:
            lines[tier.start : tier.start] = [*made, ""]
            return "\n".join(lines)
        end = len(lines)
        while end and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = [*([""] if end else []), *made]
        return "\n".join(lines)

    return want
