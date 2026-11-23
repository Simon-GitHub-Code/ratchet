#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import gatelib
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    Runner = Callable[[list[str], Path], tuple[int, float]]

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


@dataclass(frozen=True)
class SeedResult:
    kind: ReuseKind
    copied: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    survived: bool
    seconds: float
    threshold: float
    reason: str


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


def _safe_cache_rel(source: Path, dest: Path, rel: str) -> tuple[Path, Path]:
    pure = PurePosixPath(rel)
    if not rel or not pure.parts or pure.is_absolute() or ".." in pure.parts:
        message = f"cache path must be relative without `..`: `{rel}`"
        raise ValueError(message)
    src_root = source.resolve()
    dst_root = dest.resolve()
    src_path = (src_root / pure).resolve()
    dst_path = (dst_root / pure).resolve()
    if not src_path.is_relative_to(src_root) or not dst_path.is_relative_to(dst_root):
        message = f"cache path escapes tree: `{rel}`"
        raise ValueError(message)
    return source / pure, dest / pure


def _copy_cache_entry(src_path: Path, dst_path: Path) -> bool:
    if src_path.is_dir():
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_path, dst_path, dirs_exist_ok=True, symlinks=True)
        return True
    if src_path.is_file():
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        return True
    return False


def seed_worktree(source: Path, dest: Path, spec: BuildSpec) -> SeedResult:
    if spec.reuse.kind is ReuseKind.NONE:
        return SeedResult(kind=ReuseKind.NONE)
    if spec.reuse.kind is ReuseKind.SHARED:
        raw = Path(spec.reuse.env_dir).expanduser()
        resolved = (raw if raw.is_absolute() else source / raw).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return SeedResult(kind=ReuseKind.SHARED, env={spec.reuse.env_name: str(resolved)})
    copied: list[str] = []
    missing: list[str] = []
    for rel in spec.cache_paths:
        src_path, dst_path = _safe_cache_rel(source, dest, rel)
        if _copy_cache_entry(src_path, dst_path):
            copied.append(rel)
        else:
            missing.append(rel)
    return SeedResult(kind=ReuseKind.MOVED, copied=tuple(copied), missing=tuple(missing))


def seed_from_binding(source: Path, dest: Path) -> SeedResult | None:
    text = gatelib.read_text(source / gatelib.BINDING)
    if text is None:
        return None
    spec = parse_build_spec(text)
    if spec is None:
        return None
    return seed_worktree(source, dest, spec)


def _is_excluded(rel: str, cache_paths: tuple[str, ...]) -> bool:
    pure = PurePosixPath(rel)
    return any(
        pure == PurePosixPath(cache) or pure.is_relative_to(PurePosixPath(cache))
        for cache in cache_paths
    )


def _visible_paths(source: Path) -> list[str] | None:
    if gatelib.git(source, "rev-parse", "--show-toplevel") is None:
        return None
    listed = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    return [item.decode() for item in listed.stdout.split(b"\0") if item]


def _copy_tree_without_caches(source: Path, dest: Path, cache_paths: tuple[str, ...]) -> None:
    visible = _visible_paths(source)
    if visible is None:
        visible = [
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if (path.is_file() or path.is_symlink())
            and ".git" not in path.relative_to(source).parts
        ]
    for rel in visible:
        if _is_excluded(rel, cache_paths):
            continue
        src = source / rel
        if src.is_symlink() or src.is_file():
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)


def _run_timed(command: list[str], cwd: Path) -> tuple[int, float]:
    started = time.monotonic()
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, check=False)
    except OSError:
        return 127, time.monotonic() - started
    return done.returncode, time.monotonic() - started


def probe_threshold(spec: BuildSpec) -> float:
    if spec.cold_seconds <= spec.warm_seconds:
        return spec.warm_seconds * 1.25
    return (spec.warm_seconds + spec.cold_seconds) / 2.0


def probe_moved_cache(
    source: Path,
    command: list[str],
    spec: BuildSpec,
    *,
    runner: Runner | None = None,
) -> ProbeResult:
    threshold = probe_threshold(spec)
    run = runner or _run_timed
    with tempfile.TemporaryDirectory(prefix="ratchet-cache-probe-") as scratch:
        dest = Path(scratch) / "tree"
        dest.mkdir()
        _copy_tree_without_caches(source, dest, spec.cache_paths)
        seed_worktree(source, dest, spec)
        code, elapsed = run(command, dest)
    if code != 0:
        return ProbeResult(
            survived=False, seconds=elapsed, threshold=threshold, reason=f"command exited {code}"
        )
    if elapsed <= threshold:
        return ProbeResult(
            survived=True,
            seconds=elapsed,
            threshold=threshold,
            reason="moved cache stayed warm",
        )
    return ProbeResult(
        survived=False,
        seconds=elapsed,
        threshold=threshold,
        reason="moved cache was rejected and ran cold",
    )


def _cmd_seed(source: Path, dest: Path) -> int:
    result = seed_from_binding(source.resolve(), dest.resolve())
    if result is None:
        print("build_cache: no build spec")
        return 0
    if result.kind is ReuseKind.MOVED:
        print(f"build_cache: moved: copied={list(result.copied)} missing={list(result.missing)}")
    elif result.kind is ReuseKind.SHARED:
        pairs = " ".join(f"{name}={value}" for name, value in sorted(result.env.items()))
        print(f"build_cache: shared: {pairs}")
    else:
        print("build_cache: none")
    return 0


def _cmd_probe(root: Path, command: list[str]) -> int:
    cmd = command[1:] if command[:1] == ["--"] else command
    if not cmd:
        print("build_cache: probe: missing build command")
        return 2
    resolved = root.resolve()
    text = gatelib.read_text(resolved / gatelib.BINDING)
    spec = parse_build_spec(text) if text is not None else None
    if spec is None:
        print("build_cache: probe: no build spec")
        return 1
    result = probe_moved_cache(resolved, cmd, spec)
    verdict = "survived" if result.survived else "rejected"
    print(
        f"build_cache: probe: {verdict} ({result.seconds:.2f}s,"
        f" threshold {result.threshold:.2f}s): {result.reason}"
    )
    return 0 if result.survived else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="build_cache.py")
    sub = parser.add_subparsers(dest="action", required=True)
    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--source", type=Path, required=True)
    seed_parser.add_argument("--dest", type=Path, required=True)
    probe_parser = sub.add_parser("probe")
    probe_parser.add_argument("--root", type=Path, default=Path())
    probe_parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv[1:])
    if options.action == "seed":
        return _cmd_seed(options.source, options.dest)
    return _cmd_probe(options.root, options.command)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
