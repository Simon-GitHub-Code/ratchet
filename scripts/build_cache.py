from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import markdown_text

if TYPE_CHECKING:
    from collections.abc import Iterable

SPEED = "## Speed"
BUILD = "Build"
MIN_TICKED = 3
REQUIRED = ("gate", "caches", "cold", "warm", "reuse")
ALLOWED = frozenset((*REQUIRED, "consumers"))
NONE_REASON = re.compile(r"none:\s*(\S.*)")


class ReuseKind(Enum):
    MOVED = "moved"
    SHARED = "shared"
    NONE = "none"


@dataclass(frozen=True)
class ReusePolicy:
    kind: ReuseKind
    env_name: str = ""
    env_dir: str = ""
    reason: str = ""


@dataclass(frozen=True)
class BuildSpec:
    gate: str
    cache_paths: tuple[str, ...]
    cold_seconds: float
    warm_seconds: float
    reuse: ReusePolicy
    consumers: tuple[str, ...] = ()


def build_line(binding_text: str) -> str | None:
    bullets = markdown_text.bullets(markdown_text.section(binding_text, SPEED))
    return bullets.get(BUILD)


def _unbacktick(raw: str) -> str | None:
    text = raw.strip()
    if len(text) < MIN_TICKED or not (text.startswith("`") and text.endswith("`")):
        return None
    inner = text[1:-1]
    return None if "`" in inner else inner.strip()


def _ticked_items(raw: str, what: str) -> tuple[tuple[str, ...] | None, str | None]:
    text = raw.strip()
    if text == "none":
        return (), None
    items: list[str] = []
    for part in text.split(","):
        item = _unbacktick(part)
        if item is None:
            return None, f"{what} must be `none` or comma-separated backticked {what}"
        if not item or any(ch.isspace() for ch in item):
            return None, f"{what} must contain non-empty values without spaces"
        items.append(item)
    return tuple(items), None


def _parse_gate(raw: str) -> tuple[str | None, str | None]:
    items, err = _ticked_items(raw, "gate")
    if err is not None or items is None or len(items) != 1:
        return None, "gate must be one backticked gate name without spaces"
    return items[0], None


def _parse_caches(raw: str) -> tuple[tuple[str, ...] | None, str | None]:
    items, err = _ticked_items(raw, "relative paths")
    if err is not None or items is None:
        return None, err
    for item in items:
        if item.startswith("/") or ".." in PurePosixPath(item).parts:
            return None, f"caches must use relative paths without `..`: `{item}`"
    return items, None


def _parse_seconds(raw: str, *, positive: bool, label: str) -> tuple[float | None, str | None]:
    text = raw.strip()
    if text.endswith("s"):
        try:
            value = float(text[:-1])
        except ValueError:
            value = math.nan
        if math.isfinite(value) and (value > 0 if positive else value >= 0):
            return value, None
    kind = "positive" if positive else "non-negative"
    return None, f"{label} must be a {kind} number of seconds (for example `180s`)"


def _parse_reuse(raw: str) -> tuple[ReusePolicy | None, str | None]:
    text = raw.strip()
    if text == "moved":
        return ReusePolicy(kind=ReuseKind.MOVED), None
    if text.startswith("shared "):
        return _parse_shared_reuse(text[7:].strip())
    if text == "none" or text.startswith("none:"):
        reason = text[5:].strip() if text.startswith("none:") else ""
        if not reason:
            return None, "reuse `none` needs a reason after `none:`"
        return ReusePolicy(kind=ReuseKind.NONE, reason=reason), None
    return None, "reuse must be `moved`, `shared `ENV=dir``, or `none: <reason>`"


def _parse_shared_reuse(raw: str) -> tuple[ReusePolicy | None, str | None]:
    inner = _unbacktick(raw)
    if inner is None:
        return None, "reuse `shared` must name ``ENV=dir`` in backticks"
    env_name, sep, env_dir = inner.partition("=")
    if not sep or not env_name or not env_dir:
        return None, "reuse `shared` must have non-empty `ENV=dir`"
    if not env_name.isidentifier():
        return None, f"reuse `shared` environment variable `{env_name}` is not a valid identifier"
    if any(ch.isspace() for ch in env_dir):
        return None, "reuse `shared` directory must be without spaces"
    return ReusePolicy(kind=ReuseKind.SHARED, env_name=env_name, env_dir=env_dir), None


def _parse_consumers(raw: str) -> tuple[tuple[str, ...] | None, str | None]:
    return _ticked_items(raw, "gate names")


def _split_clauses(value: str) -> tuple[dict[str, str], list[str]]:
    clauses: dict[str, str] = {}
    errors: list[str] = []
    for raw_part in value.split(";"):
        part = raw_part.strip()
        if not part:
            errors.append("Build line contains an empty clause")
            continue
        key, sep, body = part.partition(" ")
        if not sep:
            errors.append(f"Build clause `{part}`: expected `<key> <value>`")
            continue
        if key not in ALLOWED:
            errors.append(f"Build line has unknown clause `{key}`")
            continue
        if key in clauses:
            errors.append(f"Build line has duplicate clause `{key}`")
            continue
        clauses[key] = body.strip()
    return clauses, errors


def _parse_spec_fields(clauses: dict[str, str]) -> tuple[BuildSpec | None, list[str]]:
    gate, gate_err = _parse_gate(clauses["gate"])
    caches, caches_err = _parse_caches(clauses["caches"])
    cold, cold_err = _parse_seconds(clauses["cold"], positive=True, label="cold")
    warm, warm_err = _parse_seconds(clauses["warm"], positive=False, label="warm")
    reuse, reuse_err = _parse_reuse(clauses["reuse"])
    consumers, cons_err = (
        _parse_consumers(clauses["consumers"]) if "consumers" in clauses else ((), None)
    )
    field_errors = [
        err
        for err in (gate_err, caches_err, cold_err, warm_err, reuse_err, cons_err)
        if err is not None
    ]
    if (
        gate is None
        or caches is None
        or cold is None
        or warm is None
        or reuse is None
        or consumers is None
    ):
        return None, field_errors
    return BuildSpec(gate, caches, cold, warm, reuse, consumers), []


def parse_build_line(value: str) -> tuple[BuildSpec | None, list[str]]:
    text = value.strip()
    if not text:
        return None, ["Build line is unfilled"]
    if text.startswith("<"):
        return None, []
    if text == "none" or text.startswith("none:"):
        if NONE_REASON.fullmatch(text):
            return None, []
        return None, ["Build line says `none` with no reason: write `none: <reason>`"]
    clauses, errors = _split_clauses(text)
    missing = [key for key in REQUIRED if key not in clauses]
    if missing:
        errors.append(f"Build line is missing required clause(s): {', '.join(missing)}")
    if errors:
        return None, errors
    return _parse_spec_fields(clauses)


def parse_build_spec(binding_text: str) -> BuildSpec | None:
    line = build_line(binding_text)
    if line is None:
        return None
    spec, errors = parse_build_line(line)
    return None if errors else spec


def validate_build_spec(
    binding_text: str, *, known_gates: Iterable[str] | None = None
) -> list[str]:
    line = build_line(binding_text)
    if line is None:
        return []
    spec, errors = parse_build_line(line)
    if errors or spec is None or known_gates is None:
        return errors
    gates = frozenset(known_gates)
    if spec.gate not in gates:
        errors.append(f"Build line names unknown gate `{spec.gate}`")
    if spec.gate in spec.consumers:
        errors.append(f"Build consumers cannot list its own gate `{spec.gate}`")
    for consumer in spec.consumers:
        if consumer != spec.gate and consumer not in gates:
            errors.append(f"Build line names unknown consumer gate `{consumer}`")
    return errors
