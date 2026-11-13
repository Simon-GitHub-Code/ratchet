"""The scripted owner's persona file: who they are, the idea, and the slice a person can use."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PERSONAS = Path(__file__).resolve().parents[1] / "personas"


@dataclass(frozen=True)
class Slice:
    """The first thing a person could do with the app, and how the harness checks it."""

    kind: str
    describe: str
    value: str
    expect: str


@dataclass(frozen=True)
class Persona:
    """One owner: a name, the idea they bring, how they think, and their first slice."""

    name: str
    idea: str
    profile: str
    slice: Slice


def load(name_or_path: str) -> Persona:
    """The persona named by a stem under ``personas/`` or by a path to a TOML file."""
    path = Path(name_or_path)
    if not path.suffix:
        path = PERSONAS / f"{name_or_path}.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    piece = data["slice"]
    return Persona(
        name=str(data["name"]),
        idea=str(data["idea"]),
        profile=str(data["profile"]).strip(),
        slice=Slice(
            kind=str(piece["kind"]),
            describe=str(piece["describe"]),
            value=str(piece["value"]),
            expect=str(piece["expect"]),
        ),
    )
