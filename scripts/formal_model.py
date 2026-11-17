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
    model: tuple[str, ...]
    mirrors: tuple[str, ...]
    synced: str


def globs(value: str) -> tuple[str, ...]:
    if value.startswith("<"):
        return ()
    return tuple(item for item in (glob.strip(" `") for glob in value.split(",")) if item)


def read(binding: str) -> Formal | None:
    lines = markdown_text.bullets(markdown_text.section(binding, SECTION))
    mirrors = globs(lines.get("Mirrors", ""))
    if not mirrors:
        return None
    synced = lines.get("Synced", "").strip(" `")
    return Formal(globs(lines.get("Model", "")), mirrors, "" if synced.startswith("<") else synced)


@dataclass(frozen=True)
class Behind:
    synced: str
    commit: str = ""


def behind(root: Path, default: str, formal: Formal) -> Behind | None:
    synced = formal.synced
    if not synced:
        return Behind("")
    if gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{synced}^{{commit}}") is None:
        return Behind(synced[:SHORT])
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
    return Behind(synced[:SHORT], hit[:SHORT]) if hit else None


def started(root: Path) -> bool:
    refs = gatelib.git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/heads/{NAMESPACE}",
        f"refs/remotes/origin/{NAMESPACE}",
    )
    return bool(refs)


def waiting(root: Path, default: str) -> str | None:
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
