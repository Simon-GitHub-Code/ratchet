"""The binding's formal model, and whether it has fallen behind the code it mirrors (ADR 0062).

``## Formal verification`` holds three lines: ``- Model:`` (globs of the models, harness and
skill), ``- Mirrors:`` (globs of the code they describe) and ``- Synced: <commit>``, the default
branch commit the last round checked. The model is **behind** when a first-parent commit of the
default branch that ``- Synced:`` cannot reach touches a mirrored path. Merge order and dates play
no part, and a clone without the ``- Synced:`` commit reads as behind and says why.

A branch the safeguard makes for a model lives under ``formal/``: ``formal/upkeep-<commit>`` for a
round's change, which waits for the owner's merge; ``formal/kept-`` branches never wait.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import gatelib
import markdown_text

if TYPE_CHECKING:
    from pathlib import Path

SECTION = "## Formal verification"
UPKEEP = "formal/upkeep-"
NAMESPACE = "formal"
SHORT = 12


@dataclass(frozen=True)
class Formal:
    """The three lines of the binding's ``## Formal verification`` section."""

    model: tuple[str, ...]
    mirrors: tuple[str, ...]
    synced: str


def globs(value: str) -> tuple[str, ...]:
    """The comma-separated globs of a line, backticks and blanks dropped."""
    return tuple(item for item in (glob.strip(" `") for glob in value.split(",")) if item)


def read(binding: str) -> Formal | None:
    """The section of ``binding``; ``None`` when it is absent or mirrors nothing."""
    lines = markdown_text.bullets(markdown_text.section(binding, SECTION))
    mirrors = globs(lines.get("Mirrors", ""))
    if not mirrors:
        return None
    return Formal(globs(lines.get("Model", "")), mirrors, lines.get("Synced", "").strip(" `"))


def behind(root: Path, default: str, formal: Formal) -> str | None:
    """Why the model is behind the code on ``default``, or ``None`` when it is in sync."""
    synced = formal.synced
    if not synced:
        return "the binding's `- Synced:` line names no commit"
    if gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{synced}^{{commit}}") is None:
        return (
            f"this clone does not hold the `- Synced:` commit {synced[:SHORT]}: fetch the full"
            " history of the default branch"
        )
    hit = gatelib.git(
        root,
        "log",
        "-1",
        "--first-parent",
        "--no-renames",
        "--format=%H",
        f"{synced}..{default}",
        "--",
        *(f":(glob){mirror}" for mirror in formal.mirrors),
    )
    if not hit:
        return None
    return (
        f"commit {hit[:SHORT]} on `{default}` changed code the model mirrors since the"
        f" `- Synced:` commit {synced[:SHORT]}"
    )


def started(root: Path) -> bool:
    """Whether any branch under ``formal/`` exists here or on ``origin``."""
    refs = gatelib.git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{NAMESPACE}",
        f"refs/remotes/origin/{NAMESPACE}",
    )
    return bool(refs)


def waiting(root: Path, default: str) -> str | None:
    """The oldest ``formal/upkeep-`` branch the default branch does not hold, if there is one."""
    listed = gatelib.git(
        root,
        "branch",
        "--no-merged",
        default,
        "--list",
        f"{UPKEEP}*",
        "--sort=committerdate",
        "--format=%(refname:short)",
    )
    names = (listed or "").split()
    return names[0] if names else None
