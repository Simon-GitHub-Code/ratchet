from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PERSONAS = Path(__file__).resolve().parents[1] / "personas"


@dataclass(frozen=True)
class Slice:
    kind: str
    describe: str
    value: str
    expect: str
    then: str = ""


@dataclass(frozen=True)
class Persona:
    name: str
    idea: str
    profile: str
    slice: Slice
    stack: str = ""


def load(name_or_path: str) -> Persona:
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
            then=str(piece.get("then", "")),
        ),
        stack=str(data.get("stack", "")),
    )


def names() -> list[str]:
    return sorted(p.stem for p in PERSONAS.glob("*.toml"))
