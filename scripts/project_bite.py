#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import functools
import os
import queue
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NamedTuple

import application_types
import budget
import gatelib
import owed
import plugin_pin
import report
import runrecord
import suites

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import diff as guard_paths
from ratchet_guards.manifest import Manifest

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

REGISTRY = Path("quality/bites.toml")
RULE = "bites"
MISSING = "missing; every gate bound to a tool needs a [[bite]]"
PATCHES = "quality/bites/"
KINDS = ("write", "patch", "remove", "replace")
GATE_GROUPS = ("tool-config", "gate-commands", "fitness", "gate-tools", "formal")
GATE_PATHS = (str(REGISTRY), f"{PATCHES}**", str(gatelib.BINDING))
PULL_REQUEST_TIERS = ("0", "1")
REAL_TREE = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "VIRTUAL_ENV")
IDENTITY = ("-c", "user.name=bite", "-c", "user.email=bite@example.invalid")
COMMIT = (*IDENTITY, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m")
BITING = "RATCHET_BITING"
SNAPSHOT = ".git/bite-index"
FRESH = ".git/bite-fresh-index"
MAX_LINK_HOPS = 40
EXCLUDE = ".git/info/exclude"
INDEX = ".git/index"
RULES = (".gitignore", ".gitattributes")
CHECK = ".git/bite-check-index"
FILE_MODES = {"100644": 0o644, "100755": 0o755}
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW
DESCRIPTORS = (
    bool(NOFOLLOW)
    and DIRECTORY != os.O_RDONLY | NOFOLLOW
    and {os.open, os.mkdir, os.rmdir, os.stat, os.symlink, os.unlink} <= os.supports_dir_fd
    and os.listdir in os.supports_fd
    and shutil.rmtree.avoids_symlink_attacks
)
UNSUPPORTED = (
    "this system cannot open a directory without following a link, so the scratch copy is"
    " neither written nor put back"
)
TAIL = 15
MAX_LANES = 4
GATE_ROW_CELLS = 5
GATE_LIMIT = 900.0
RUN_CEILING = "bite-run"
SLOW_CEILING = "tier0"
CURE = (
    "a bite of a whole-tree gate is narrowed: its args name one file, one target or one mutant,"
    " or its `whole` says why it cannot be"
)
SLOW = 2.0
SLOW_FLOOR = 1.0
WAKE = 0.1
GLOB_SPECIAL = re.compile(r"[\\*?\[]")
SHIPPED = re.compile(r"`ratchet` plugin `[^`]+`(?: and `[^`]+`)*")


class Unprovable(Exception):  # noqa: N818 # -- a verdict on the run, not a fault of the code
    ...


@dataclass(frozen=True)
class Row:
    question: str
    tool: str
    tier: str
    command: str
    limit: float = GATE_LIMIT
    line: str = field(default="", compare=False)

    @property
    def gate(self) -> str:
        return self.command.split()[-1] if self.command else ""

    @property
    def bound(self) -> bool:
        waiting = application_types.NONE.match(self.tool) or application_types.LATER.match(
            self.tool
        )
        return bool(self.tool) and not waiting and not SHIPPED.fullmatch(self.tool)


def gate_rows(binding: str) -> list[Row]:
    return list(split_gates(binding)[0])


@functools.lru_cache(maxsize=256)
def split_gates(binding: str) -> tuple[tuple[Row, ...], str]:
    before, marker, body = binding.partition("\n## Gates\n")
    section, divider, after = body.partition("\n## ")
    kept: list[str] = []
    rows: list[Row] = []
    header = False
    for line in section.splitlines(keepends=True):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("|") or set("".join(cells)) <= set("-: "):
            kept.append(line)
        elif not header:
            header = True
            kept.append(line)
        elif len(cells) >= GATE_ROW_CELLS:
            command = cells[4].partition("`")[2].partition("`")[0]
            rows.append(Row(cells[0], cells[1], cells[2], command, line="|".join(cells)))
        else:
            kept.append(line)
    return tuple(rows), before + marker + "".join(kept) + divider + after


def run_git(
    root: Path,
    *args: str,
    index: Path | None = None,
    stdin: str | bytes | None = None,
    store: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
        input=stdin.encode() if isinstance(stdin, str) else stdin,
        env=environment() | ({"GIT_INDEX_FILE": str(index)} if index else {}) | (store or {}),
    )


def git_bytes(root: Path, *args: str, index: Path | None = None, stdin: str | None = None) -> bytes:
    result = run_git(root, *args, index=index, stdin=stdin)
    if result.returncode != 0:
        message = f"git {' '.join(args)} failed in {root}: {result.stderr.decode().strip()}"
        raise Unprovable(message)
    return result.stdout


def git_in(root: Path, *args: str, index: Path | None = None, stdin: str | None = None) -> str:
    return git_bytes(root, *args, index=index, stdin=stdin).decode().strip()


def environment() -> dict[str, str]:
    kept = {key: value for key, value in os.environ.items() if key not in (*REAL_TREE, report.RUN)}
    return {**kept, BITING: "1"}


@dataclass(frozen=True)
class Write:
    path: str
    content: str

    def plant(self, copy: Tree) -> None:
        owned(copy.path, self.path)
        copy.write(self.path, self.content.encode(), create=creatable(self.path))
        git_in(copy.path, "add", "--intent-to-add", "--", self.path)


@dataclass(frozen=True)
class Patch:
    path: str

    def plant(self, copy: Tree) -> None:
        check = copy.path / CHECK
        copy.write(CHECK, copy.read(SNAPSHOT) or b"")
        git_in(copy.path, "update-index", "-q", "--refresh", index=check)
        git_in(copy.path, "apply", "--check", "--index", self.path, index=check)
        git_in(copy.path, "apply", "--cached", "--whitespace=nowarn", self.path, index=check)
        before, after = staged(copy.path, copy.path / SNAPSHOT), staged(copy.path, check)
        changed = sorted(
            path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
        )
        owned(copy.path, *changed)
        for path in changed:
            if path not in after:
                copy.remove(path)
                continue
            mode, blob = after[path]
            if mode not in FILE_MODES:
                message = (
                    f"{path} would be a symbolic link or a submodule, and a fault writes a file"
                )
                raise Unprovable(message)
            content = git_bytes(copy.path, "cat-file", "--filters", f"--path={path}", blob)
            copy.write(path, content, mode=FILE_MODES[mode], create=creatable(path))
        added = [path for path in changed if path not in before]
        if added:
            git_in(copy.path, "add", "--intent-to-add", "--", *added)


@dataclass(frozen=True)
class Remove:
    patterns: tuple[str, ...]

    def plant(self, copy: Tree) -> None:
        found = [p for p in visible(copy.path) if gatelib.matches(p, list(self.patterns))]
        files = [p for p in found if (copy.path / p).is_file()]
        if not files:
            message = (
                f"no file matches {', '.join(self.patterns)}: the collection is empty already, "
                "and the gate was green on it"
            )
            raise Unprovable(message)
        owned(copy.path, *files)
        for path in files:
            copy.remove(path)
        parents = {str(PurePosixPath(path).parent) for path in files} - {"."}
        for directory in sorted(parents, reverse=True):
            copy.prune(directory)


@dataclass(frozen=True)
class Swap:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Replace:
    swaps: tuple[Swap, ...]

    def plant(self, copy: Tree) -> None:
        for swap in self.swaps:
            owned(copy.path, swap.path)
            content = copy.read(swap.path)
            if content is None:
                message = f"{swap.path} is not a file in the copy, and 'replace' changes a file"
                raise Unprovable(message)
            copy.write(swap.path, swapped(content, swap))


def swapped(content: bytes, swap: Swap) -> bytes:
    old = swap.old.encode()
    first = content.find(old)
    if first == -1:
        message = (
            f"{swap.path} no longer holds the text the fault replaces; write 'old' as the"
            f" file has it now: {swap.old!r}"
        )
        raise Unprovable(message)
    if content.find(old, first + 1) != -1:
        message = (
            f"{swap.path} holds the text the fault replaces in more than one place;"
            f" lengthen 'old' until it names one: {swap.old!r}"
        )
        raise Unprovable(message)
    return content[:first] + swap.new.encode() + content[first + len(old) :]


Fault = Write | Patch | Remove | Replace


def owned(copy: Path, *paths: str) -> None:
    for path in paths:
        parts = PurePosixPath(path).parts
        if not inside(path) or ".git" in (part.casefold() for part in parts):
            message = f"{path} is not a path in the work tree git sees"
            raise Unprovable(message)
        if any(copy.joinpath(*parts[:depth]).is_symlink() for depth in range(1, len(parts) + 1)):
            raise Unprovable(linked(path))
    parents = sorted({name for path in paths for name in above(path)})
    staged = git_in(copy, "ls-files", "-z", "--stage", "--", *parents) if parents else ""
    gitlinks = {line.partition("\t")[2] for line in staged.split("\0") if line.startswith("160000")}
    for path in paths:
        for name in above(path):
            if name in gitlinks or (copy / name / ".git").exists():
                message = (
                    f"{path} is inside {name}, a repository of its own, whose files restoring"
                    " the copy does not put back"
                )
                raise Unprovable(message)
    ignored = run_git(
        copy, "check-ignore", "-z", "--stdin", index=copy / SNAPSHOT, stdin="\0".join(paths)
    )
    if ignored.returncode != 1:
        named = ignored.stdout.decode().partition("\0")[0] or " or ".join(paths)
        message = f"{named} is ignored by git, and restoring the copy puts back only what git sees"
        raise Unprovable(message)


def above(path: str) -> list[str]:
    parts = PurePosixPath(path).parts
    return ["/".join(parts[:depth]) for depth in range(1, len(parts))]


def staged(copy: Path, index: Path) -> dict[str, tuple[str, str]]:
    listed = git_in(copy, "ls-files", "-z", "--stage", index=index).split("\0")
    entries = [line.partition("\t") for line in listed if line]
    return {path: (entry.split()[0], entry.split()[1]) for entry, _, path in entries}


def creatable(path: str) -> bool:
    return PurePosixPath(path).name not in RULES


def linked(path: str) -> str:
    return f"{path} is reached through a symbolic link; a fault changes the copy's own file"


@dataclass(frozen=True)
class Tree:
    path: Path
    made: tuple[int, int]

    @classmethod
    def pin(cls, path: Path) -> Tree:
        found = path.lstat()
        return cls(path, (found.st_dev, found.st_ino))

    @contextmanager
    def at(self, path: str, *, make: bool = False) -> Iterator[tuple[int, str]]:
        if not DESCRIPTORS:
            raise Unprovable(UNSUPPORTED)
        *way, name = PurePosixPath(path).parts
        here = os.open(self.path, DIRECTORY)
        try:
            found = os.fstat(here)
            if (found.st_dev, found.st_ino) != self.made:
                message = f"{self.path} is no longer the directory the scratch copy was made in"
                raise Unprovable(message)
            for depth, part in enumerate(way, 1):
                if make:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, dir_fd=here)
                try:
                    below = os.open(part, DIRECTORY, dir_fd=here)
                except OSError as error:
                    where = "/".join(way[:depth])
                    raise refusal(error, path, at=(here, part), where=where) from error
                os.close(here)
                here = below
            yield here, name
        finally:
            os.close(here)

    def read(self, path: str) -> bytes | None:
        with self.at(path) as (here, name):
            try:
                opened = os.open(name, os.O_RDONLY | os.O_NONBLOCK | NOFOLLOW, dir_fd=here)
            except FileNotFoundError:
                return None
            except OSError as error:
                raise refusal(error, path, at=(here, name), where=path) from error
            if not stat.S_ISREG(os.fstat(opened).st_mode):
                os.close(opened)
                return None
            with os.fdopen(opened, "rb") as file:
                return file.read()

    def write(
        self, path: str, content: bytes, *, mode: int | None = None, create: bool = True
    ) -> None:
        with self.at(path, make=True) as (here, name):
            old = entry(here, name)
            if old is None and not create:
                message = (
                    f"{path} would be a new {name}, and restoring the copy works by those rules"
                )
                raise Unprovable(message)
            if old is not None:
                if stat.S_ISLNK(old.st_mode):
                    raise Unprovable(linked(path))
                os.unlink(name, dir_fd=here)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW
            with os.fdopen(os.open(name, flags, 0o666, dir_fd=here), "wb") as file:
                file.write(content)
                kept = stat.S_IMODE(old.st_mode) if old is not None and mode is None else mode
                if kept is not None:
                    os.fchmod(file.fileno(), kept)

    def remove(self, path: str) -> None:
        with self.at(path) as (here, name):
            old = entry(here, name)
            if old is not None and stat.S_ISLNK(old.st_mode):
                raise Unprovable(linked(path))
            os.unlink(name, dir_fd=here)

    def clear(self, path: str) -> None:
        with self.at(path.rstrip("/")) as (here, name):
            self.empty(here, name)

    def put(self, path: str, mode: str, content: bytes) -> None:
        with self.at(path, make=True) as (here, name):
            self.empty(here, name)
            if mode == "120000":
                os.symlink(content, name, dir_fd=here)
            else:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW
                with os.fdopen(os.open(name, flags, 0o666, dir_fd=here), "wb") as file:
                    file.write(content)
                    os.fchmod(file.fileno(), FILE_MODES.get(mode, 0o644))

    @staticmethod
    def empty(here: int, name: str) -> None:
        found = entry(here, name)
        if found is None:
            return
        try:
            if stat.S_ISDIR(found.st_mode):
                shutil.rmtree(name, dir_fd=here)
            else:
                os.unlink(name, dir_fd=here)
        except OSError as error:
            message = f"failed to remove {name}: {error.strerror}"
            raise Unprovable(message) from error

    def prune(self, path: str) -> None:
        with self.at(path) as (here, name):
            opened = os.open(name, DIRECTORY, dir_fd=here)
            try:
                empty = not os.listdir(opened)  # noqa: PTH208 # -- a descriptor, not a path
            finally:
                os.close(opened)
            if empty:
                os.rmdir(name, dir_fd=here)


def entry(here: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=here, follow_symlinks=False)
    except FileNotFoundError:
        return None


def refusal(error: OSError, path: str, *, at: tuple[int, str], where: str) -> Exception:
    found = entry(*at)
    if found is not None and stat.S_ISLNK(found.st_mode):
        return Unprovable(linked(path))
    return OSError(error.errno, error.strerror, where)


@dataclass(frozen=True)
class Bite:
    gate: str
    name: str
    expect: str
    fault: Fault
    args: tuple[str, ...] = ()
    commit: bool = False
    proof: str = ""
    whole: str = field(default="", compare=False)

    def plant(self, copy: Tree) -> None:
        self.fault.plant(copy)
        if self.commit:
            git_in(copy.path, "add", "-A")
            git_in(copy.path, *COMMIT, f"test(bite): {self.name}")


@dataclass(frozen=True)
class Registry:
    setup: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    bites: tuple[Bite, ...] = ()


def within(base: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(base.resolve())


def inside(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path) and not pure.is_absolute() and ".." not in pure.parts


def strings(table: dict[str, object], key: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        message = f"'{key}' is a list of non-empty strings"
        raise ValueError(message)
    return tuple(str(item) for item in value)


def text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"'{key}' is a non-empty string"
        raise ValueError(message)
    return value


def read_swap(swap: object) -> Swap:
    if not isinstance(swap, dict) or set(swap) != {"path", "old", "new"}:
        message = "'replace' is { path = ..., old = ..., new = ... }, or a list of them"
        raise ValueError(message)
    if not inside(text(swap, "path")):
        message = "'replace' names a path inside the repository"
        raise ValueError(message)
    if not isinstance(swap["new"], str):
        message = "'new' is a string"
        raise ValueError(message)  # noqa: TRY004 # -- one kind for all that is wrong in a bite
    return Swap(swap["path"], text(swap, "old"), swap["new"])


def read_fault(table: dict[str, object]) -> Fault:
    kinds = [kind for kind in KINDS if kind in table]
    if len(kinds) != 1:
        message = "a bite plants exactly one fault: one of 'write', 'patch', 'remove' and 'replace'"
        raise ValueError(message)
    if kinds == ["replace"]:
        swaps = table["replace"]
        if swaps == []:
            message = "'replace' names no text"
            raise ValueError(message)
        return Replace(tuple(map(read_swap, swaps if isinstance(swaps, list) else [swaps])))
    if kinds == ["remove"]:
        patterns = strings(table, "remove")
        if not patterns:
            message = "'remove' names no glob"
            raise ValueError(message)
        return Remove(patterns)
    if kinds == ["patch"]:
        path = text(table, "patch")
        if not (inside(path) and path.startswith(PATCHES)):
            message = f"'patch' is a path under {PATCHES}, where the manifest guards it"
            raise ValueError(message)
        return Patch(path)
    write = table["write"]
    if not isinstance(write, dict) or set(write) != {"path", "content"}:
        message = "'write' is { path = ..., content = ... }"
        raise ValueError(message)
    if not inside(text(write, "path")):
        message = "'write' names a path inside the repository"
        raise ValueError(message)
    content = write["content"]
    return Write(
        write["path"], content if isinstance(content, str) else "".join(strings(write, "content"))
    )


def read_bite(table: dict[str, object]) -> Bite:
    keys = {"gate", "name", "expect", "args", "commit", "proof", "whole", *KINDS}
    unknown = sorted(set(table) - keys)
    if unknown:
        message = f"unknown key '{unknown[0]}'"
        raise ValueError(message)
    commit = table.get("commit", False)
    if not isinstance(commit, bool):
        message = "'commit' is true or false"
        raise ValueError(message)  # noqa: TRY004 # -- one kind for all that is wrong in a bite
    args = strings(table, "args")
    whole = text(table, "whole") if "whole" in table else ""
    if args and whole:
        message = "a bite narrows its gate with 'args' or says in 'whole' why it cannot, not both"
        raise ValueError(message)
    return Bite(
        gate=text(table, "gate"),
        name=text(table, "name"),
        expect=text(table, "expect"),
        fault=read_fault(table),
        args=args,
        commit=commit,
        proof=text(table, "proof") if "proof" in table else "",
        whole=whole,
    )


def read_registry(source: str) -> tuple[Registry, list[str]]:
    try:
        data: dict[str, object] = tomllib.loads(source)
    except tomllib.TOMLDecodeError as error:
        return Registry(), [str(error)]
    problems = [f"unknown key '{key}'" for key in sorted(set(data) - {"setup", "include", "bite"})]
    tables = data.get("bite", [])
    if not isinstance(tables, list) or not all(isinstance(table, dict) for table in tables):
        return Registry(), [*problems, "'bite' is written as [[bite]] tables"]
    bites: list[Bite] = []
    for index, table in enumerate(tables):
        try:
            bites.append(read_bite(table))
        except ValueError as error:
            problems.append(f"bite {index + 1} ({table.get('gate', '?')}): {error}")
    try:
        registry = Registry(strings(data, "setup"), strings(data, "include"), tuple(bites))
    except ValueError as error:
        return Registry(bites=tuple(bites)), [*problems, str(error)]
    if not all(inside(path) for path in registry.include):
        problems.append("'include' names paths inside the repository")
    return registry, problems


def misplaced(root: Path, rows: Sequence[Row], bites: Sequence[Bite]) -> Iterator[str]:
    gates = {row.gate for row in rows}
    seen: set[tuple[str, str]] = set()
    for bite in bites:
        if bite.gate not in gates:
            yield f"bite '{bite.name}' names gate '{bite.gate}', which is no row's entry point"
        if (bite.gate, bite.name) in seen:
            yield f"gate '{bite.gate}' has two bites named '{bite.name}'"
        seen.add((bite.gate, bite.name))
        if isinstance(bite.fault, Patch) and not (root / bite.fault.path).is_file():
            yield f"bite '{bite.name}' applies {bite.fault.path}, which is missing"


class Unplantable(NamedTuple):
    bite: Bite
    why: str

    @property
    def identity(self) -> str:
        return f"bite '{self.bite.name}' of gate '{self.bite.gate}' cannot be planted"

    def __str__(self) -> str:
        return f"{self.identity}: {self.why}"


class Unchecked(NamedTuple):
    bite: Bite
    where: str

    def __str__(self) -> str:
        return (
            f"bite '{self.bite.name}' of gate '{self.bite.gate}': nothing git sees is at"
            f" {self.where}, and the registry's `setup` may make it in the scratch copy; the full"
            " proof plants it"
        )


class Unplanted(NamedTuple):
    why: str
    unseen: tuple[str, ...] = ()
    rest_plants: bool = False


MOVED = "if the file moved, {}, or else remove the bite and add one that plants"


def unplanted(
    root: Path, seen: set[str], fault: Fault, *, store: dict[str, str]
) -> Unplanted | None:
    if isinstance(fault, Patch) and (root / fault.path).is_file():
        return unapplied(root, seen, fault.path, store=store)
    if isinstance(fault, Replace):
        return unreplaced(root, seen, fault)
    if isinstance(fault, Remove) and not any(gatelib.matches(p, [*fault.patterns]) for p in seen):
        globs = ", ".join(fault.patterns)
        why = (
            f"no file git sees matches {globs}; write the globs to match the collection as it is"
            " now"
        )
        return Unplanted(why, fault.patterns, rest_plants=True)
    return None


def unapplied(root: Path, seen: set[str], patch: str, *, store: dict[str, str]) -> Unplanted | None:
    result = run_git(root, "apply", "--check", "--cached", patch, store=store)
    if result.returncode == 0:
        return None
    unseen = tuple(sorted(patch_paths(root, (root / patch).read_bytes()) - seen - {"**"}))
    excluded = ["--exclude=" + GLOB_SPECIAL.sub(r"\\\g<0>", path) for path in unseen]
    rest = run_git(root, "apply", "--check", "--cached", *excluded, patch, store=store)
    if unseen and rest.returncode == 0:
        why = f"{patch} changes {' and '.join(unseen)}, which git does not see; " + MOVED.format(
            "make the patch again against its new name"
        )
        return Unplanted(why, unseen, rest_plants=True)
    errors = "; ".join(line for line in rest.stderr.decode().splitlines() if line)
    why = f"{patch} no longer applies: {errors}; make the patch again against the file as it is now"
    return Unplanted(why)


def unreplaced(root: Path, seen: set[str], fault: Replace) -> Unplanted | None:
    paths = dict.fromkeys(swap.path for swap in fault.swaps)
    contents = {path: (root / path).read_bytes() for path in paths if path in seen}
    for swap in fault.swaps:
        if swap.path in contents:
            try:
                contents[swap.path] = swapped(contents[swap.path], swap)
            except Unprovable as error:
                return Unplanted(str(error))
    unseen = tuple(path for path in paths if path not in contents)
    if not unseen:
        return None
    why = (
        f"{unseen[0]} is not a file git sees, and a fault plants only into what git sees; "
        + MOVED.format("point 'path' at its new name")
    )
    return Unplanted(why, unseen, rest_plants=True)


@contextmanager
def as_git_sees(root: Path) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="ratchet-bite-check-") as scratch:
        index, objects = Path(scratch) / "index", Path(scratch) / "objects"
        objects.mkdir()
        store = {
            "GIT_INDEX_FILE": str(index),
            "GIT_OBJECT_DIRECTORY": str(objects),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(git_path(root, "objects")),
        }
        with contextlib.suppress(FileNotFoundError):
            shutil.copyfile(git_path(root, "index"), index)
        run_git(root, "add", "--all", store=store)
        yield store


def committed(root: Path) -> set[str]:
    refs = ("HEAD", gatelib.base_commit(root))
    return {path for ref in refs if ref for path in gatelib.tracked_paths(root, ref)}


def plants(root: Path, registry: Registry) -> tuple[list[Unplantable], list[Unchecked]]:
    if gatelib.git(root, "rev-parse", "--show-toplevel") is None:
        return [], []
    seen = set(gatelib.tracked_paths(root))
    patches = any(isinstance(bite.fault, Patch) for bite in registry.bites)
    sight: contextlib.AbstractContextManager[dict[str, str]] = (
        as_git_sees(root) if patches else contextlib.nullcontext({})
    )
    with sight as store:
        found = [(bite, unplanted(root, seen, bite.fault, store=store)) for bite in registry.bites]
    judged = [(bite, verdict) for bite, verdict in found if verdict is not None]
    made = bool(registry.setup) and any(verdict.rest_plants for _, verdict in judged)
    known = committed(root) if made else set()
    unplantable: list[Unplantable] = []
    unchecked: list[Unchecked] = []
    for bite, verdict in judged:
        gone = any(gatelib.matches(path, [*verdict.unseen]) for path in known)
        if made and verdict.rest_plants and not gone:
            unchecked.append(Unchecked(bite, ", ".join(verdict.unseen)))
        else:
            unplantable.append(Unplantable(bite, verdict.why))
    return unplantable, unchecked


def checked(root: Path, registry: Registry) -> tuple[list[Unplantable], dict[str, bool]]:
    unplantable, unchecked = plants(root, registry)
    for found in unchecked:
        print(f"bite: not checked: {found}")
    skipped = {found.bite.gate for found in unchecked}
    return unplantable, {bite.gate: bite.gate not in skipped for bite in registry.bites}


def unproven(
    rows: Sequence[Row], bites: Sequence[Bite], catalogue: application_types.Catalogue
) -> Iterator[str]:
    for row in rows:
        mine = [bite for bite in bites if bite.gate == row.gate]
        if not row.bound:
            continue
        if not mine:
            yield (
                f"gate '{row.gate}' ({row.question}) is bound to a tool and no [[bite]] plants "
                "a fault for it"
            )
            continue
        emptied = any(isinstance(bite.fault, Remove) for bite in mine)
        if row.question in catalogue.collections and not emptied:
            yield (
                f"gate '{row.gate}' ({row.question}) judges a collection and no [[bite]] uses "
                "'remove' to show it red when the collection is empty"
            )
        claimed = {bite.proof for bite in mine}
        for proof in catalogue.proofs.get(row.question, ()):
            if proof not in claimed:
                yield (
                    f"gate '{row.gate}' ({row.question}) has no [[bite]] with proof = \"{proof}\": "
                    f"{catalogue.meanings[proof]}"
                )


def inspect(root: Path) -> tuple[list[Row], Registry, list[str], list[str]]:
    rows = gate_rows(gatelib.read_text(root / gatelib.BINDING) or "")
    source = gatelib.read_text(root / REGISTRY)
    registry, problems = read_registry(source) if source is not None else (Registry(), [MISSING])
    problems += misplaced(root, rows, registry.bites)
    gaps = list(unproven(rows, registry.bites, application_types.load()))
    return rows, registry, problems, gaps


class RegistryCheck(NamedTuple):
    failures: list[str | Unplantable]
    unchecked: list[Unchecked]


def registry_check(root: Path) -> RegistryCheck:
    _, registry, problems, gaps = inspect(root)
    unplantable, unchecked = plants(root, registry)
    return RegistryCheck([*problems, *gaps, *unplantable], unchecked)


def failures(root: Path) -> list[str]:
    return [f"{REGISTRY}: {problem}" for problem in registry_check(root).failures]


def owed_key(problem: str | Unplantable) -> owed.Key:
    return (RULE, str(REGISTRY), problem if isinstance(problem, str) else problem.identity)


def owing(root: Path, problems: Sequence[str | Unplantable]) -> tuple[list[str], list[str]]:
    if not problems:
        return [], []
    ledger = owed.read(root)
    entries = [entry for entry in ledger.entries if entry.rule == RULE]
    hits, _ = owed.excused([owed_key(problem) for problem in problems], entries)
    judged = list(zip(problems, hits, strict=True))
    excused = [problem for problem, hit in judged if hit]
    red = [f"{owed.LEDGER}: {problem}" for problem in ledger.problems]
    red += [f"{REGISTRY}: {problem}" for problem, hit in judged if not hit]
    if not excused:
        return red, []
    keys = {owed_key(problem) for problem in excused}
    since = ", ".join(sorted({entry.since for entry in entries if entry.key in keys}))
    gaps = "gap" if len(excused) == 1 else "gaps"
    notes = [f"bite: owed: {REGISTRY}: {problem}" for problem in excused]
    return red, [*notes, f"bite: {len(excused)} {gaps} owed since {since}, listed in {owed.LEDGER}"]


class Run(NamedTuple):
    code: int
    output: str
    seconds: float = 0.0
    timed_out: bool = False


class Shell:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.live: set[subprocess.Popen[bytes]] = set()
        self.stopped = False

    def run(self, command: Sequence[str], cwd: Path, limit: float | None = None) -> Run:
        with tempfile.TemporaryFile() as log:
            with self.lock:
                if self.stopped:
                    return Run(130, "stopped")
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=environment(),
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as error:
                    return Run(127, f"cannot run {command[0]}: {error}")
                self.live.add(process)
            started = time.monotonic()
            timed_out = False
            try:
                code = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                timed_out = True
                gatelib.stop_group(process)
                code = process.returncode
            seconds = time.monotonic() - started
            with self.lock:
                self.live.discard(process)
            left = gatelib.end_group(process.pid)
            log.seek(0)
            output = log.read().decode("utf-8", errors="replace")
        output += f"bite: timed out after {limit:g}s\n" if timed_out else ""
        return Run(
            code, output + (f"bite: {gatelib.LEFT_RUNNING}\n" if left else ""), seconds, timed_out
        )

    def stop(self) -> None:
        with self.lock:
            self.stopped = True
            live = list(self.live)
        for process in live:
            gatelib.stop_group(process)


def visible(root: Path) -> list[str]:
    listed = git_in(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [path for path in listed.split("\0") if path]


def carry(source: Path, target: Path) -> None:
    if source.is_symlink() or source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def carry_included(root: Path, copy: Path, path: str) -> None:
    if not within(root, root / path):
        message = f"include '{path}' leaves the repository through a symbolic link"
        raise Unprovable(message)
    if (root / path).is_dir():
        shutil.copytree(root / path, copy / path, symlinks=True, dirs_exist_ok=True)
    carry(root / path, copy / path)


class Verdict(Enum):
    BITES = "bites"
    GREEN = "DOES NOT BITE"
    RED_BEFORE = "RED BEFORE THE FAULT"
    OTHER_REASON = "RED FOR ANOTHER REASON"
    NOT_PLANTED = "THE FAULT WAS NOT PLANTED"
    NOT_PROVEN = "NOT PROVEN"
    TIMED_OUT = "TIMED OUT"


@dataclass(frozen=True)
class Outcome:
    bite: Bite
    verdict: Verdict
    detail: str = ""
    seconds: float = 0.0
    lane: int = 0
    red_seconds: float = 0.0
    slower: float = 0.0

    def lines(self) -> str:
        note = f"; {self.slower:.1f}x this gate's fastest green run" if self.slower > SLOW else ""
        head = (
            f"bite: {self.bite.gate}: {self.bite.name}: {self.verdict.value}"
            f" ({self.seconds:.1f}s, lane {self.lane}{note})"
        )
        return "\n".join([head, *(f"  {line}" for line in self.detail.splitlines())])

    def record(self) -> dict[str, object]:
        return {
            "kind": runrecord.FAULT,
            "gate": self.bite.gate,
            "bite": self.bite.name,
            "seconds": round(self.seconds, 3),
            "narrowed": bool(self.bite.args),
            "verdict": self.verdict.value,
            "lane": self.lane,
        }

    def unproven(self, later: Run) -> Outcome:
        if later.timed_out:
            return replace(self.timed_out(later.output), red_seconds=later.seconds)
        why = (
            "the gate did not return to green once the fault was gone, so its red may come from"
            " what its own runs leave and not from the fault"
        )
        return replace(
            self,
            verdict=Verdict.NOT_PROVEN,
            detail=f"{why}\n{tail(later.output)}",
            red_seconds=later.seconds,
        )

    def timed_out(self, output: str) -> Outcome:
        return replace(self, verdict=Verdict.TIMED_OUT, detail=tail(output))


def tail(output: str) -> str:
    return "\n".join(output.splitlines()[-TAIL:])


def judge(bite: Bite, green: str, planted: Run) -> Outcome:
    code, output = planted.code, planted.output
    if planted.timed_out:
        return Outcome(bite, Verdict.TIMED_OUT, tail(output))
    if code == 0:
        why = "the gate is green with the fault planted"
        return Outcome(bite, Verdict.GREEN, f"{why}\n{tail(output)}")
    seen, before = output.count(bite.expect), green.count(bite.expect)
    if seen > before:
        return Outcome(bite, Verdict.BITES)
    why = (
        f"the gate is red, and '{bite.expect}' is in its output as often as when it is green"
        if seen
        else f"the gate is red, and its output lacks '{bite.expect}'"
    )
    return Outcome(bite, Verdict.OTHER_REASON, f"{why}\n{tail(output)}")


@dataclass(frozen=True)
class Copy:
    tree: Tree
    shell: Shell
    head: str
    snapshot_tree: str
    staged: bytes
    exclude: bytes
    lane: int = 0

    @property
    def path(self) -> Path:
        return self.tree.path

    def restore(self) -> None:
        snapshot = self.path / SNAPSHOT
        git_in(self.path, "reset", "-q", "--soft", self.head)
        self.tree.write(INDEX, self.staged)
        self.tree.write(EXCLUDE, self.exclude)
        fresh = self.path / FRESH
        git_in(self.path, "read-tree", self.snapshot_tree, index=fresh)
        run_git(self.path, "update-index", "-q", "--refresh", index=fresh)
        listed = ("ls-files", "-z", "--others", "--directory", "--exclude-standard")
        for path in git_in(self.path, *listed, index=snapshot).split("\0"):
            if path:
                self.tree.clear(path)
        held = staged(self.path, snapshot)
        listed = ("diff-files", "-z", "--raw", "--no-renames", "--ignore-submodules=all")
        fields = git_in(self.path, *listed, index=fresh).split("\0")
        for path in sorted(fields[1::2]):
            mode, blob = held[path]
            if mode in FILE_MODES:
                content = git_bytes(self.path, "cat-file", "--filters", f"--path={path}", blob)
            else:
                content = git_bytes(self.path, "cat-file", "blob", blob)
            self.tree.put(path, mode, content)
        contained(self.path, self.head)

    def run(self, command: Sequence[str], limit: float) -> Run:
        result = self.shell.run(command, self.path, limit)
        self.restore()
        return result

    def prove(self, row: Row, bites: Sequence[Bite]) -> Iterator[Outcome]:
        outcomes: list[Outcome] = []
        greens: list[float] = []
        for bite in bites:
            if self.shell.stopped:
                return
            before = self.unfaulted(row, bite.args, outcomes, greens=greens)
            green = before.output
            if before.code != 0:
                verdict = Verdict.TIMED_OUT if before.timed_out else Verdict.RED_BEFORE
                outcomes.append(
                    self.outcome(
                        bite,
                        verdict,
                        detail=tail(green),
                        seconds=before.seconds,
                        red=before.seconds,
                    )
                )
                continue
            try:
                bite.plant(self.tree)
            except (Unprovable, OSError) as error:
                self.restore()
                why = str(error).replace(f"{self.path}/", "")
                outcomes.append(
                    self.outcome(bite, Verdict.NOT_PLANTED, detail=why, seconds=before.seconds)
                )
                continue
            planted = self.run([*shlex.split(row.command), *bite.args], row.limit)
            judged = judge(bite, green, planted)
            outcomes.append(
                replace(
                    judged,
                    seconds=before.seconds + planted.seconds,
                    lane=self.lane,
                    red_seconds=planted.seconds,
                )
            )
        for args in dict.fromkeys(o.bite.args for o in outcomes if o.verdict is Verdict.BITES):
            self.unfaulted(row, args, outcomes, greens=greens)
        fastest = min(greens, default=0.0)
        if not self.shell.stopped:
            yield from (
                replace(o, slower=o.red_seconds / fastest)
                if fastest and o.verdict is not Verdict.BITES and o.red_seconds >= SLOW_FLOOR
                else o
                for o in outcomes
            )

    def outcome(
        self, bite: Bite, verdict: Verdict, *, detail: str, seconds: float, red: float = 0.0
    ) -> Outcome:
        return Outcome(bite, verdict, detail, seconds, self.lane, red)

    def unfaulted(
        self, row: Row, args: tuple[str, ...], outcomes: list[Outcome], *, greens: list[float]
    ) -> Run:
        result = self.run([*shlex.split(row.command), *args], row.limit)
        if result.code == 0:
            greens.append(result.seconds)
        else:
            outcomes[:] = [
                o.unproven(result) if o.verdict is Verdict.BITES and o.bite.args == args else o
                for o in outcomes
            ]
        return result


def git_path(root: Path, name: str) -> Path:
    return Path(git_in(root, "rev-parse", "--path-format=absolute", "--git-path", name))


def make_copy(root: Path, copy: Path, *, registry: Registry, shell: Shell, lane: int = 0) -> Copy:
    git_in(root, "init", "-q", str(copy))
    tree = Tree.pin(copy)
    alternates = f"{git_path(root, 'objects')}\n"
    (copy / ".git/objects/info/alternates").write_text(alternates, encoding="utf-8")
    git_in(copy, "fetch", "-q", "--update-head-ok", str(root), "+refs/*:refs/*")
    head = git_in(root, "rev-parse", "HEAD")
    git_in(copy, "update-ref", "--no-deref", "HEAD", head)
    remote_head = gatelib.git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if remote_head:
        git_in(copy, "symbolic-ref", "refs/remotes/origin/HEAD", remote_head)
    for name in ("index", "info/exclude"):
        carry(git_path(root, name), copy / ".git" / name)
    for path in visible(root):
        carry(root / path, copy / path)
    for path in registry.include:
        carry_included(root, copy, path)
    for name in gatelib.default_branches(copy):
        ref = f"refs/remotes/{name}" if name.startswith("origin/") else f"refs/heads/{name}"
        if gatelib.git(copy, "rev-parse", "-q", "--verify", ref) is not None:
            git_in(copy, "update-ref", ref, head)
    for command in registry.setup:
        result = shell.run(shlex.split(command), copy, GATE_LIMIT)
        if result.code != 0:
            how = "timed out" if result.timed_out else f"exited {result.code}"
            message = f"setup `{command}` {how} in the scratch copy\n{tail(result.output)}"
            raise Unprovable(message)
    snapshot(copy)
    made = Copy(
        tree,
        shell,
        git_in(copy, "rev-parse", "HEAD"),
        git_in(copy, "write-tree", index=copy / SNAPSHOT),
        tree.read(INDEX) or b"",
        tree.read(EXCLUDE) or b"",
        lane,
    )
    made.restore()
    return made


def snapshot(copy: Path) -> None:
    git_in(copy, "add", "--all", index=copy / SNAPSHOT)
    listed = git_in(copy, "ls-files", "-z", "--cached", "--ignored", "--exclude-standard")
    tracked = [path for path in listed.split("\0") if path and os.path.lexists(copy / path)]
    if tracked:
        git_in(
            copy,
            "--literal-pathspecs",
            "add",
            "--force",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            index=copy / SNAPSHOT,
            stdin="\0".join(tracked),
        )
    contained(copy, git_in(copy, "rev-parse", "HEAD"))


def under(absolute: str, bases: tuple[str, ...]) -> list[str] | None:
    for base in bases:
        if absolute == base or absolute.startswith(base + os.sep):
            return absolute[len(base) :].split("/")
    return None


def ends_inside(path: str, targets: dict[str, str], bases: tuple[str, ...], *, copy: Path) -> bool:
    pending: list[str] = path.split("/")
    here: list[str] = []
    hops = 0
    while pending:
        part = pending.pop(0)
        if part == "..":
            if not here:
                return False
            here.pop()
        elif part not in ("", "."):
            name = "/".join([*here, part])
            target = targets.get(name)
            if target is None and (copy / name).is_symlink():
                target = str((copy / name).readlink())
            if target is None:
                here.append(part)
                continue
            hops += 1
            below = under(target, bases) if target.startswith("/") else None
            if hops > MAX_LINK_HOPS or (target.startswith("/") and below is None):
                return False
            if below is not None:
                here = []
            pending = (below if below is not None else target.split("/")) + pending
    return True


def contained(copy: Path, head: str) -> None:
    def links(*args: str, index: Path | None = None) -> dict[str, str]:
        listed = git_in(copy, "ls-files" if index else "ls-tree", "-z", *args, index=index)
        found = {}
        for line in listed.split("\0"):
            head_part, _, path = line.partition("\t")
            fields = head_part.split()
            if fields and fields[0] == "120000" and path:
                found[path] = fields[2] if index is None else fields[1]
        return found

    committed = links("-r", head)
    snapshot = links("--stage", index=copy / SNAPSHOT)
    snapshot_links = {
        path: git_bytes(copy, "cat-file", "blob", oid).decode(errors="surrogateescape")
        for path, oid in snapshot.items()
    }
    bases = (os.path.realpath(copy), str(copy))
    for path, oid in snapshot.items():
        if committed.get(path) != oid and not ends_inside(path, snapshot_links, bases, copy=copy):
            message = (
                f"setup left {path}, a symbolic link that leads outside the scratch copy"
                " (to the real tree or elsewhere); a later write through it would land there"
            )
            raise Unprovable(message)


Task = tuple[Row, list[Bite]]


def run_limit(root: Path, override: float | None) -> tuple[float, str]:
    if override is not None:
        return override, f"bite: limit: {override:g}s a run"
    file = root / gatelib.THRESHOLDS
    text = file.read_text(encoding="utf-8") if file.is_file() else ""
    ceiling = budget.ceiling_of(text, RUN_CEILING)
    key = f"`{RUN_CEILING}` in {gatelib.THRESHOLDS}"
    if ceiling is not None:
        return ceiling, f"bite: limit: {ceiling:g}s a run, {key}"
    return GATE_LIMIT, f"bite: limit: {GATE_LIMIT:g}s a run; no {key}"


def slow_ceiling(root: Path) -> float | None:
    try:
        return budget.ceiling_of(gatelib.read_text(root / gatelib.THRESHOLDS) or "", SLOW_CEILING)
    except tomllib.TOMLDecodeError:
        return None


def slow_line(bite: Bite, seconds: float, ceiling: float) -> str:
    cure = f"whole on purpose: {bite.whole}" if bite.whole else CURE
    return (
        f"{bite.gate}: {bite.name}: {seconds:.1f}s with no args, over the {SLOW_CEILING}"
        f" ceiling of {ceiling:g}s; {cure}"
    )


def recorded_slow(root: Path) -> list[str]:
    ceiling = slow_ceiling(root)
    if ceiling is None:
        return []
    last = {
        (record.get("gate"), record.get("bite")): record
        for record in runrecord.read(root / runrecord.TIMING)
        if record.get("kind") == runrecord.FAULT
    }
    registry, _ = read_registry(gatelib.read_text(root / REGISTRY) or "")
    lines = []
    for bite in registry.bites:
        record = last.get((bite.gate, bite.name), {})
        seconds = record.get("seconds")
        whole = record.get("narrowed") is False and not (bite.args or bite.whole)
        if whole and isinstance(seconds, float) and seconds > ceiling:
            lines.append(f"{REGISTRY}: {slow_line(bite, seconds, ceiling)}")
    return lines


def run_lanes(
    root: Path, registry: Registry, tasks: Sequence[Task], *, lanes: int, limit: float
) -> list[Outcome]:
    stamp = runrecord.stamp(root)
    pending: queue.SimpleQueue[Task] = queue.SimpleQueue()
    for row, bites in tasks:
        pending.put((replace(row, limit=limit), bites))
    shell = Shell()
    printing = threading.Lock()

    def lane(number: int, path: Path, made: Copy | None) -> list[Outcome]:
        copy = made or make_copy(root, path, registry=registry, shell=shell, lane=number)
        outcomes: list[Outcome] = []
        while not shell.stopped:
            try:
                row, bites = pending.get_nowait()
            except queue.Empty:
                break
            for outcome in copy.prove(row, bites):
                with printing:
                    print(outcome.lines(), flush=True)
                    runrecord.append(root / runrecord.TIMING, {**outcome.record(), **stamp})
                outcomes.append(outcome)
        return outcomes

    with (
        tempfile.TemporaryDirectory(prefix="ratchet-bite-", ignore_cleanup_errors=True) as scratch,
        ThreadPoolExecutor(max_workers=lanes, thread_name_prefix="bite-lane") as pool,
    ):
        paths = [Path(scratch) / f"copy-{n}" for n in range(lanes)]
        try:
            first = make_copy(root, paths[0], registry=registry, shell=shell)
            with gatelib.signals_held():
                futures = [
                    pool.submit(lane, n, path, None if n else first) for n, path in enumerate(paths)
                ]
            while wait(futures, timeout=WAKE).not_done:
                pass
            return [outcome for future in futures for outcome in future.result()]
        except BaseException:
            shell.stop()
            raise


def load() -> str:
    average = getattr(os, "getloadavg", None)
    if average is None:
        return "unknown"
    return f"{average()[0]:.1f} on {os.cpu_count()} cores"


def proof(
    root: Path, registry: Registry, tasks: Sequence[Task], *, lanes: int, limit: float | None
) -> int:
    started = time.monotonic()
    load_before = load()
    seconds, first = run_limit(root, limit)
    print(first)
    try:
        with gatelib.interrupts_raise():
            outcomes = run_lanes(root, registry, tasks, lanes=lanes, limit=seconds)
    except (Unprovable, OSError) as error:
        return gatelib.report("bite", [str(error)])
    except gatelib.Interrupted as stop:
        print(f"bite: red: {stop}")
        return 128 + stop.signum
    ceiling = slow_ceiling(root)
    if ceiling is not None:
        for outcome in outcomes:
            if not outcome.bite.args and outcome.seconds > ceiling:
                print(f"bite: slow: {slow_line(outcome.bite, outcome.seconds, ceiling)}")
    unseen = [outcome for outcome in outcomes if outcome.verdict is not Verdict.BITES]
    print(
        f"bite: {'red' if unseen else 'green'}: {len(outcomes) - len(unseen)} of {len(outcomes)} "
        f"faults bit, over {len(tasks)} gates in {time.monotonic() - started:.0f}s"
    )
    if unseen:
        print(f"bite: load: {load_before} at the start, {load()} at the end")
    return 1 if unseen else 0


class Touched(NamedTuple):
    path: str
    group: str


class Cause(NamedTuple):
    gate: str | None
    why: str


Targets = dict[tuple[bool, str], set[tuple[str, str]]]
SUITES_GATE = "tests-changed"
IGNORE_NAMES = (".gitignore", ".gitattributes")
JUSTFILE_NAMES = ("justfile", ".justfile", "Justfile")
READER = {"gate-commands": "runs it"}
READS = "## What each gate reads"
READS_BULLET = re.compile(r"^- ([^:\s]+):[ \t]*(.*)$")


def defining_group(names: set[str]) -> str:
    wide = [name for name in GATE_GROUPS if name in names and name != "tool-config"]
    return wide[0] if wide else "tool-config" if "tool-config" in names else ""


def gate_paths_changed(root: Path, base: str | None) -> list[Touched] | None:
    if base is None:
        return None
    git = guard_paths.git_in(root)
    paths = guard_paths.changed_paths(git, base)
    manifests = guard_paths.manifests(git, base, root)
    targets = fault_targets(root, base)
    touched = []
    for path in paths:
        names = {
            group.name
            for manifest in manifests
            for group in manifest.groups
            if Manifest((group,)).group_of(path)
        }
        if gatelib.matches(path, list(GATE_PATHS)):
            touched.append(Touched(path, ""))
        elif defining_group(names):
            touched.append(Touched(path, defining_group(names)))
        elif PurePosixPath(path).name in IGNORE_NAMES:
            touched.append(Touched(path, "ignore-rules"))
        elif targeted_by(targets, path):
            touched.append(Touched(path, "fault"))
    return touched


def registries(root: Path, base: str) -> tuple[Registry | None, Registry | None]:
    found = []
    for source in (gatelib.show(root, base, str(REGISTRY)), gatelib.read_text(root / REGISTRY)):
        registry, problems = read_registry(source) if source is not None else (None, [MISSING])
        found.append(None if problems else registry)
    return found[0], found[1]


def registry_causes(root: Path, base: str) -> list[Cause]:
    old, new = registries(root, base)
    if old is None or new is None:
        return [Cause(None, f"{REGISTRY} was added, removed or does not parse")]
    if (old.setup, old.include) != (new.setup, new.include):
        return [Cause(None, f"{REGISTRY} changed `setup` or `include`, which every copy holds")]
    before = {(bite.gate, bite.name): bite for bite in old.bites}
    after = {(bite.gate, bite.name): bite for bite in new.bites}
    causes = []
    for key in sorted(before.keys() | after.keys()):
        if before.get(key) != after.get(key):
            verb = "added" if key not in before else "removed" if key not in after else "changed"
            causes.append(Cause(key[0], f"bite `{key[1]}` {verb}"))
    return causes


def patch_causes(root: Path, base: str, path: str) -> list[Cause]:
    bites = [bite for registry in registries(root, base) if registry for bite in registry.bites]
    named = {
        (bite.gate, bite.name)
        for bite in bites
        if isinstance(bite.fault, Patch) and bite.fault.path == path
    }
    if not named:
        why = f"{path} is under {PATCHES} and no bite names it as its patch, so setup may run it"
        return [Cause(None, why)]
    return [Cause(gate, f"patch {path} is named by bite `{name}`") for gate, name in sorted(named)]


def patch_paths(root: Path, patch: bytes | None) -> set[str]:
    if not patch:
        return set()
    forward = run_git(root, "apply", "--numstat", "-z", stdin=patch)
    backward = run_git(root, "apply", "-R", "--numstat", "-z", stdin=patch)
    if forward.returncode != 0 or backward.returncode != 0:
        return {"**"}
    fields = (forward.stdout + backward.stdout).decode(errors="replace").split("\0")
    return {field.rpartition("\t")[2] for field in fields if field}


def committed_bytes(root: Path, base: str, path: str) -> bytes | None:
    result = run_git(root, "show", f"{base}:{path}")
    return result.stdout if result.returncode == 0 else None


def fault_targets(root: Path, base: str) -> Targets:
    targets: Targets = {}
    for registry in registries(root, base):
        for bite in registry.bites if registry else ():
            fault = bite.fault
            if isinstance(fault, Write):
                names, glob = {fault.path}, False
            elif isinstance(fault, Replace):
                names, glob = {swap.path for swap in fault.swaps}, False
            elif isinstance(fault, Remove):
                names, glob = set(fault.patterns), True
            else:
                head = (root / fault.path).read_bytes() if (root / fault.path).is_file() else None
                names = patch_paths(root, committed_bytes(root, base, fault.path))
                names |= patch_paths(root, head)
                glob = False
            for name in names:
                targets.setdefault((glob or name == "**", name), set()).add((bite.gate, bite.name))
    return targets


def targeted_by(targets: Targets, path: str) -> set[tuple[str, str]]:
    return {
        who
        for (pattern, name), bites in targets.items()
        if (gatelib.matches(path, [name]) if pattern else name == path)
        for who in bites
    }


def binding_causes(root: Path, base: str) -> list[Cause]:
    binding = str(gatelib.BINDING)
    old = gatelib.show(root, base, binding) or ""
    new = gatelib.read_text(root / binding) or ""
    old_suites, old = suites.split(old)
    new_suites, new = suites.split(new)
    (old_rows, old_rest), (new_rows, new_rest) = split_gates(old), split_gates(new)
    if (
        plugin_pin.PIN_LINE.sub("", old_rest).strip()
        != plugin_pin.PIN_LINE.sub("", new_rest).strip()
    ):
        return [Cause(None, f"{binding} changed outside its gate rows, which a gate may read")]
    if old_rest.strip() != new_rest.strip():
        return [Cause(None, "the plugin pin changed, and the plugin supplies gates")]
    if any(len({row.question for row in rows}) != len(rows) for rows in (old_rows, new_rows)):
        why = f"{binding} has two gate rows for one question, so one could hide behind the other"
        return [Cause(None, why)]
    before = {row.question: row for row in old_rows}
    after = {row.question: row for row in new_rows}
    causes = []
    for question in sorted(before.keys() | after.keys()):
        was, now = before.get(question), after.get(question)
        if was is not None and now is not None and was.line == now.line:
            continue
        verb = "added" if was is None else "removed" if now is None else "changed"
        rows = [row for row in (was, now) if row is not None]
        if not all(row.gate for row in rows):
            why = f"the binding's row `{question}` {verb} and names no gate"
            return [Cause(None, why)]
        causes += [Cause(row.gate, f"the binding's row `{question}` {verb}") for row in rows]
    changed = sorted(
        name
        for name in old_suites.keys() | new_suites.keys()
        if old_suites.get(name) != new_suites.get(name)
    )
    return causes + [
        Cause(SUITES_GATE, f"the binding's suites row `{name}` changed") for name in changed
    ]


def read_reads(binding: str) -> tuple[dict[str, list[str]], list[str]]:
    _, marker, body = binding.partition(f"\n{READS}\n")
    reads: dict[str, list[str]] = {}
    strays: list[str] = []
    gate = None
    for line in body.partition("\n## ")[0].splitlines() if marker else []:
        found = READS_BULLET.match(line)
        if found:
            gate, text = found[1], found[2]
            reads.setdefault(gate, [])
        elif gate is not None and line.strip() and line[0] in " \t":
            text = line
        else:
            text = ""
            if gate is not None and line.strip():
                strays.append(line.strip())
        if gate is not None:
            entries = (entry.strip().strip("`") for entry in text.split(","))
            reads[gate].extend(entry for entry in entries if entry)
    return reads, strays


def gate_reads(binding: str) -> dict[str, list[str]]:
    return read_reads(binding)[0]


def reading(path: str, entry: str) -> bool:
    return gatelib.matches(path, [entry, f"{entry.rstrip('/')}/**"])


def unnamed_causes(root: Path, path: str, group: str) -> list[Cause]:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    reads = gate_reads(binding) if group == "tool-config" else {}
    if not reads:
        who = READER.get(group, "reads it")
        return [Cause(None, f"{path} changed, and no record says which gate {who}")]
    if not any(reading(path, entry) for entries in reads.values() for entry in entries):
        return [Cause(None, f"{path} changed, and no bullet names it")]
    causes = []
    for row in pull_request_gates(gate_rows(binding)):
        entries = reads.get(row.gate)
        if entries is None:
            why = f"{path} changed, and `{row.gate}` has no bullet under {READS}"
            causes.append(Cause(row.gate, why))
        elif any(reading(path, entry) for entry in entries):
            causes.append(Cause(row.gate, f"{path} changed, and `{row.gate}` reads it"))
    return causes


def could_affect(root: Path, base: str | None, touched: Sequence[Touched] | None) -> list[Cause]:
    if base is None or touched is None:
        return [Cause(None, "there is no merge base to compare with")]
    causes: list[Cause] = []
    targets = fault_targets(root, base)
    for path, group in touched:
        if path == str(REGISTRY):
            causes += registry_causes(root, base)
        elif gatelib.matches(path, [f"{PATCHES}**"]):
            causes += patch_causes(root, base, path)
        elif path == str(gatelib.BINDING):
            causes += binding_causes(root, base)
        elif PurePosixPath(path).name in JUSTFILE_NAMES:
            name = PurePosixPath(path).name
            why = f"the {name} changed, and its recipes are code the proof cannot read"
            causes.append(Cause(None, why))
        elif PurePosixPath(path).name in IGNORE_NAMES:
            why = f"{path} changed, and the proof reads the ignore rules when it plants a fault"
            causes.append(Cause(None, why))
        elif group != "fault":
            causes += unnamed_causes(root, path, group)
        causes += [
            Cause(gate, f"{path} is changed by bite `{name}`")
            for gate, name in sorted(targeted_by(targets, path))
        ]
    return causes


def pull_request_gates(rows: Sequence[Row]) -> list[Row]:
    return [row for row in rows if row.tier in PULL_REQUEST_TIERS]


def fallback(rows: Sequence[Row]) -> list[Row]:
    return pull_request_gates(rows)


def targeted(rows: Sequence[Row], causes: Sequence[Cause]) -> list[Row]:
    if any(cause.gate is None for cause in causes):
        return fallback(rows)
    named = {cause.gate for cause in causes}
    return [row for row in pull_request_gates(rows) if row.gate in named]


def explain(rows: Sequence[Row], causes: Sequence[Cause]) -> list[str]:
    if any(cause.gate is None for cause in causes):
        whys = dict.fromkeys(cause.why for cause in causes if cause.gate is None)
        return [f"bite: fallback: {'; '.join(whys)}; proving every gate"]
    return [
        f"bite: {row.gate}: "
        + "; ".join(dict.fromkeys(cause.why for cause in causes if cause.gate == row.gate))
        for row in targeted(rows, causes)
    ]


def deferred(rows: Sequence[Row], causes: Sequence[Cause], planted: dict[str, bool]) -> list[str]:
    lines = []
    for row in rows:
        whys = dict.fromkeys(cause.why for cause in causes if cause.gate == row.gate)
        if row.tier in PULL_REQUEST_TIERS or not whys or row.gate not in planted:
            continue
        plant = (
            "each of its patch, replace and remove faults was checked to still plant"
            if planted[row.gate]
            else "a fault of it was not checked to plant, as said above"
        )
        lines.append(
            f"bite: {row.gate}: {'; '.join(whys)}; a gate of tier {row.tier}, which a pull request"
            f" does not prove, and {plant}"
        )
    return lines


def changed_gates(root: Path, rows: Sequence[Row], planted: dict[str, bool]) -> list[Row] | int:
    base = gatelib.base_commit(root)
    if gatelib.adoption(root, base):
        print(
            "bite: green: this change is the adoption, whose base holds no binding; nothing is"
            " proven here, and `bite-all` proves every gate in tier 2"
        )
        return 0
    touched = gate_paths_changed(root, base)
    if touched == []:
        print("bite: green: the change touches no path that defines a gate; nothing to prove")
        return 0
    print(
        "bite: a gate may have changed"
        f" ({', '.join(t.path for t in touched or []) or 'no merge base'})"
    )
    causes = could_affect(root, base, touched)
    chosen = targeted(rows, causes)
    later = deferred(rows, causes, planted)
    if not chosen:
        nothing = "bite: green: the change affects no gate of tiers 0 and 1; nothing to prove"
        print("\n".join([*later, nothing]))
        return 0
    print("\n".join([*explain(rows, causes), *later]))
    return chosen


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="project_bite.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--changed", action="store_true", help="only the gates a change affects")
    parser.add_argument(
        "--pull-request-gates",
        action="store_true",
        help="every gate of tiers 0 and 1, whatever the change",
    )
    parser.add_argument("--gate", action="append", default=[], help="only this gate's bites")
    parser.add_argument("--jobs", type=int, default=min(MAX_LANES, gatelib.default_jobs()))
    parser.add_argument("--limit", type=float, help="seconds a run may take, for every gate")
    options = parser.parse_args(argv[1:])
    if options.changed and options.pull_request_gates:
        parser.error("--changed and --pull-request-gates are two ways to choose the gates")
    if os.environ.get(BITING):
        return gatelib.report("bite", ["a bite cannot plant a fault for the gate that runs it"])
    root = options.root.resolve()
    rows, registry, problems, gaps = inspect(root)
    problems += [] if options.gate and not options.changed else gaps
    partial = options.changed or options.pull_request_gates
    unplantable, planted = checked(root, registry) if partial else ([], {})
    red, notes = owing(root, [*problems, *unplantable])
    for line in notes:
        print(line)
    if red:
        return gatelib.report("bite", red)
    chosen = pull_request_gates(rows) if options.pull_request_gates else rows
    if options.changed:
        found = changed_gates(root, rows, planted)
        if isinstance(found, int):
            return found
        chosen = found
    tasks: list[Task] = [
        (row, [bite for bite in registry.bites if bite.gate == row.gate])
        for row in chosen
        if not options.gate or row.gate in options.gate
    ]
    tasks = [task for task in tasks if task[1]]
    if not tasks and options.changed:
        print("bite: green: the gates the change affects have no bite; nothing to prove")
        return 0
    if not tasks:
        return gatelib.report("bite", ["no bite to run: a gate with nothing to run is red"])
    lanes = max(1, min(options.jobs, len(tasks)))
    return proof(root, registry, tasks, lanes=lanes, limit=options.limit)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
