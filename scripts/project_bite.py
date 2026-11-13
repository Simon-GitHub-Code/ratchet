#!/usr/bin/env python3
r"""Every gate this repository bound, against a planted fault: red with it, green without it.

The method ships no tool, so nothing it ships can vouch for a repository's own gates. The
repository does: ``quality/bites.toml`` holds one or more ``[[bite]]`` per gate the binding binds
to a tool, each a fault to plant, and this script plants each one in a scratch copy and requires
the gate red. A gate that judges a collection (tests, benchmarks, fuzz targets) also carries a
bite that removes the collection, since a gate with nothing to run is red (ADR 0020). A question
may also require a named proof, a fault only its gate can catch
(``skills/method/application-types.toml``): a bite claims it with ``proof``. ``bite.py`` is the
other half: the gates the plugin ships, against their synthetic violations.

The registry::

    setup = ["pnpm install --frozen-lockfile --offline"]  # run once in each scratch copy
    include = [".env.test"]              # ignored paths a gate needs that setup cannot rebuild

    [[bite]]
    gate = "lint"                        # the last word of the Gates row's Entry point
    name = "an unused import"
    write = { path = "src/pkg/_bite.py", content = "import os\n" }
    expect = "F401"                      # what the gate prints when it is red on this fault

    [[bite]]
    gate = "perf"
    name = "no benchmark"
    remove = ["tests/bench/**"]          # the empty collection
    expect = "no benchmark"

    [[bite]]
    gate = "pins"
    name = "a floating range"
    replace = { path = "package.json", old = '"vitest": "5.1.0"', new = '"vitest": "^5.1.0"' }
    expect = "floating range"

    [[bite]]
    gate = "telemetry"
    name = "a route registered past the seam"
    proof = "outside-the-seam"           # the named proof this question requires
    patch = "quality/bites/route-past-the-seam.patch"
    expect = "emits no span"

A fault is exactly one of ``write`` (create or replace a file), ``replace`` (in a file that
exists, the one place ``old`` stands becomes ``new``; a list of them for several places),
``patch`` (a diff under ``quality/bites/``, applied with ``git apply``) and ``remove`` (delete
every file a glob matches). A ``replace`` keeps applying while the lines around ``old`` change,
where a patch's context lines break on the first unrelated edit beside them. A fault that no
longer applies (a patch that does not apply, ``old`` standing nowhere or in two places) is
``THE FAULT WAS NOT PLANTED``, which is red. So is a fault aimed at anything but a file git sees
in the copy, reached through no symbolic link and in no submodule (an ignored file, ``.git``, a
new ``.gitignore``): restoring the copy puts back only what git sees, and a fault elsewhere would
stay planted for the next bite. A file a fault changes is written anew, so another name for the
same data (a hard link) keeps what it held. ``args`` are appended to the gate's command, and
``commit = true`` commits the fault in the copy first, for a gate that reads history or the
index.

For each bite the script runs the gate in the copy and requires green, then plants the fault, runs
the gate again, and requires red with ``expect`` in its output more often than in that green run
(a recipe's own text is echoed on every run). No green run serves two bites: what an earlier
fault's run left behind would be judged as this fault. After each run the copy is put back as it
stood: every file git sees, the index and ``HEAD``. One line per bite: ``bites``, ``DOES NOT BITE``
(green with the fault), ``RED BEFORE THE FAULT``, ``RED FOR ANOTHER REASON`` (red, and ``expect``
no more often than when green) or ``THE FAULT WAS NOT PLANTED``. The run is red on anything but
``bites``, on a registry that does not parse, on a tool-bound gate with no bite, and on one
whose question requires a proof no bite of it claims.

A scratch copy is the work tree as git sees it (tracked and untracked files, nothing ignored), as
a repository of its own under ``$TMPDIR`` with the same index and commit, borrowing the object
store read-only. ``HEAD`` is detached there and the default branch's refs point at it, so a gate
over the change sees what the tree holds beyond ``HEAD``: once the work is committed, the planted
fault and nothing else. The real work tree is never written. What is ignored is not copied, on
purpose: a virtualenv or a build cache holds the absolute paths of the tree it was built in, and
a gate run through one would judge the real tree. ``setup`` rebuilds them in the copy from the
lockfile, as a CI job does. So a gate must build what it runs, the first run in a copy is a cold
one, and whatever lives outside the tree (a port, a database, a tool reached by an absolute path)
is shared with the real one. A submodule's files are not copied. Gates run at once, one copy per
lane, never two in one copy, at most ``--jobs`` lanes: ``RATCHET_JOBS`` by default, the number
that caps the gates of a tier, else the CPU count, and never more than four. The first copy is
set up alone and the others once it stands, so a cache ``setup`` fills outside the tree (a
package store, a compiler cache) is filled once. Put
the build in ``setup`` as well: a gate's own build is then incremental, where a cold one would
spend a service gate's boot timeout. What a command started and left running is ended when the
command exits.

Usage: ``project_bite.py [--root DIR] [--changed] [--gate NAME]... [--jobs N]``. ``--changed`` is
the pull-request form: it proves nothing unless the change touches a path that defines a gate
(the manifest's ``tool-config``, ``gate-commands``, ``fitness``, ``gate-tools`` and ``formal``
groups, the registry and the binding), and then proves the gates of tiers 0 and 1. It proves
nothing on the adoption either, the change whose base holds no binding: every gate is new
there, and the whole proof runs once in tier 2.
"""

from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import application_types
import gatelib

# Why: the guard-diff gate lives with the guard, under hooks/, as gatelib lives here for it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import diff as guard_paths

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

REGISTRY = Path("quality/bites.toml")
PATCHES = "quality/bites/"
KINDS = ("write", "patch", "remove", "replace")
# Why: a path in one of these defines what a gate runs or judges. A threshold or a register entry
# is what a gate compares against, and `ratchet-lowered` and the snapshots judge those.
GATE_GROUPS = ("tool-config", "gate-commands", "fitness", "gate-tools", "formal")
GATE_PATHS = (str(REGISTRY), f"{PATCHES}**", str(gatelib.BINDING))
PULL_REQUEST_TIERS = ("0", "1")
# Why: a hook or an activated environment points these at the real tree, and a gate in the copy
# must judge the copy.
REAL_TREE = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "VIRTUAL_ENV")
IDENTITY = ("-c", "user.name=bite", "-c", "user.email=bite@example.invalid")
COMMIT = (*IDENTITY, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m")
# Why: a bite of the gate that runs this script would run it inside itself, without end.
BITING = "RATCHET_BITING"
# Why: the copy as it stood after setup, as an index of its own: restoring from it rewrites only
# the files a run changed, so no build tool sees a tree of new timestamps.
SNAPSHOT = ".git/bite-index"
TAIL = 15
MAX_LANES = 4
# Seconds the main thread waits on the lanes at a time: the most a signal waits for its handler.
WAKE = 0.1
# Why: a Tool cell that is the plugin's script and nothing else is a gate `bite.py` proves.
SHIPPED = re.compile(r"`ratchet` plugin `[^`]+`(?: and `[^`]+`)*")


class Unprovable(Exception):  # noqa: N818 # -- a verdict on the run, not a fault of the code
    """The proof could not be attempted: no copy, a failed setup, a fault that does not apply."""


@dataclass(frozen=True)
class Row:
    """One row of the binding's Gates table."""

    question: str
    tool: str
    tier: str
    command: str

    @property
    def gate(self) -> str:
        """The entry point's name: the last word of its command."""
        return self.command.split()[-1] if self.command else ""

    @property
    def bound(self) -> bool:
        """Whether the repository chose what answers it: not ``none``, ``later:`` or shipped."""
        # Why: ADR 0028. A `later:` row answers nothing yet, so it needs no bite until it is bound.
        waiting = application_types.NONE.match(self.tool) or application_types.LATER.match(
            self.tool
        )
        return bool(self.tool) and not waiting and not SHIPPED.fullmatch(self.tool)


def gate_rows(binding: str) -> list[Row]:
    """The rows of the ``## Gates`` table; a row short of its five cells is left out."""
    _, _, body = binding.partition("\n## Gates\n")
    lines = [line for line in body.split("\n## ")[0].splitlines() if line.startswith("|")]
    table = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [cells for cells in table if not set("".join(cells)) <= set("-: ")]
    return [
        Row(cells[0], cells[1], cells[2], cells[4].partition("`")[2].partition("`")[0])
        for cells in rows[1:]
        if len(cells) > 4  # noqa: PLR2004 # -- Question, Tool, Tier, Threshold source, Entry point
    ]


def run_git(root: Path, *args: str, index: Path | None = None) -> subprocess.CompletedProcess[str]:
    """``git -C root args``, judging ``root`` and no other tree, through ``index`` when given."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment() | ({"GIT_INDEX_FILE": str(index)} if index else {}),
    )


def git_in(root: Path, *args: str, index: Path | None = None) -> str:
    """The stdout of ``run_git``; raises ``Unprovable`` when git fails."""
    result = run_git(root, *args, index=index)
    if result.returncode != 0:
        message = f"git {' '.join(args)} failed in {root}: {result.stderr.strip()}"
        raise Unprovable(message)
    return result.stdout.strip()


def environment() -> dict[str, str]:
    kept = {key: value for key, value in os.environ.items() if key not in REAL_TREE}
    return {**kept, BITING: "1"}


@dataclass(frozen=True)
class Write:
    """Create or replace one file."""

    path: str
    content: str

    def plant(self, copy: Path) -> None:
        """Write the file, as a change git sees."""
        put(owned(copy, self.path), self.content.encode())
        git_in(copy, "add", "--intent-to-add", "--", self.path)


@dataclass(frozen=True)
class Patch:
    """Apply a unified diff kept under ``quality/bites/``."""

    path: str

    def plant(self, copy: Path) -> None:
        """Apply the diff; a new file in it is a change git sees."""
        # Why: `git apply` writes any file of the work tree, ignored ones too. With `--index` it
        # finds every file the diff reads in the copy as it stood, which holds only what git sees.
        check = copy / ".git/bite-check-index"
        shutil.copyfile(copy / SNAPSHOT, check)
        git_in(copy, "update-index", "-q", "--refresh", index=check)
        git_in(copy, "apply", "--check", "--index", self.path, index=check)
        for written in git_in(copy, "apply", "--numstat", "-z", self.path).split("\0"):
            if written:
                owned(copy, written.split("\t", 2)[-1])
        git_in(copy, "apply", "--intent-to-add", "--whitespace=nowarn", self.path)


@dataclass(frozen=True)
class Remove:
    """Delete every file a glob matches: the collection a gate judges, emptied."""

    patterns: tuple[str, ...]

    def plant(self, copy: Path) -> None:
        """Delete the files; ``Unprovable`` when the globs match none."""
        found = [p for p in visible(copy) if gatelib.matches(p, list(self.patterns))]
        paths = [copy / p for p in found if (copy / p).is_file()]
        if not paths:
            message = (
                f"no file matches {', '.join(self.patterns)}: the collection is empty already, "
                "and the gate was green on it"
            )
            raise Unprovable(message)
        for path in paths:
            path.unlink()
        # Why: git holds no empty directory, so a collection with no file has no directory either.
        for directory in sorted({path.parent for path in paths}, reverse=True):
            if directory != copy and not any(directory.iterdir()):
                directory.rmdir()


@dataclass(frozen=True)
class Swap:
    """In one file, the one place ``old`` stands becomes ``new``."""

    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Replace:
    """Exact text replaced in files that exist: a fault that holds while the lines around change."""

    swaps: tuple[Swap, ...]

    def plant(self, copy: Path) -> None:
        """Replace each text; ``Unprovable`` when one stands nowhere or in more than one place."""
        for swap in self.swaps:
            target = owned(copy, swap.path)
            if not target.is_file():
                message = f"{swap.path} is not a file in the copy, and 'replace' changes a file"
                raise Unprovable(message)
            content, old = target.read_bytes(), swap.old.encode()
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
            put(target, content[:first] + swap.new.encode() + content[first + len(old) :])


Fault = Write | Patch | Remove | Replace


def owned(copy: Path, path: str) -> Path:
    """``path`` in the copy, when a fault may change it: a file git sees, reached through no link.

    Restoring the copy puts back only what git sees, so a fault anywhere else would stay planted
    for the runs after it. ``Unprovable`` says why ``path`` is not such a file.
    """
    parts = PurePosixPath(path).parts
    if not inside(path) or ".git" in (part.casefold() for part in parts):
        message = f"{path} is not a path in the work tree git sees"
        raise Unprovable(message)
    target = copy.joinpath(*parts)
    if any(copy.joinpath(*parts[:depth]).is_symlink() for depth in range(1, len(parts) + 1)):
        message = f"{path} is reached through a symbolic link; a fault changes the copy's own file"
        raise Unprovable(message)
    parents = ["/".join(parts[:depth]) for depth in range(1, len(parts))]
    staged = git_in(copy, "ls-files", "-z", "--stage", "--", *parents) if parents else ""
    gitlinks = [line.partition("\t")[2] for line in staged.split("\0") if line.startswith("160000")]
    for name in set(gitlinks).intersection(parents):
        message = f"{path} is inside the submodule {name}, whose files a copy does not hold"
        raise Unprovable(message)
    if parts[-1] == ".gitignore" and not target.exists():
        message = f"{path} would be a new .gitignore, and restoring the copy cleans by those rules"
        raise Unprovable(message)
    # Why: 1 is git's answer for a path it does not ignore; 0 is ignored, and more is no answer.
    if run_git(copy, "check-ignore", "-q", "--", path).returncode != 1:
        message = f"{path} is ignored by git, and restoring the copy puts back only what git sees"
        raise Unprovable(message)
    return target


def put(target: Path, content: bytes) -> None:
    """``content`` as a new file at ``target``, with the mode the file had.

    A file written in place would change every other name for it too (a hard link a setup made),
    and restoring the copy gives back only the name git sees.
    """
    mode = target.stat().st_mode if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.write_bytes(content)
    if mode is not None:
        target.chmod(stat.S_IMODE(mode))


@dataclass(frozen=True)
class Bite:
    """One ``[[bite]]``: a fault, the gate that must go red on it, and the words that say why."""

    gate: str
    name: str
    expect: str
    fault: Fault
    args: tuple[str, ...] = ()
    commit: bool = False
    proof: str = ""

    def plant(self, copy: Path) -> None:
        """Plant the fault in ``copy`` as a change git sees, committed when the bite says so."""
        self.fault.plant(copy)
        if self.commit:
            git_in(copy, "add", "-A")
            git_in(copy, *COMMIT, f"test(bite): {self.name}")


@dataclass(frozen=True)
class Registry:
    """``quality/bites.toml``, parsed."""

    setup: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    bites: tuple[Bite, ...] = ()


def within(base: Path, path: Path) -> bool:
    """Whether ``path``, with every symbolic link on the way followed, is still under ``base``."""
    return path.resolve().is_relative_to(base.resolve())


def inside(path: str) -> bool:
    """Whether a registry path stays in the repository."""
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
    """One ``{ path, old, new }`` of a ``replace`` fault."""
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
    """One ``[[bite]]`` table as a ``Bite``; ``ValueError`` says what is wrong with it."""
    unknown = sorted(set(table) - {"gate", "name", "expect", "args", "commit", "proof", *KINDS})
    if unknown:
        message = f"unknown key '{unknown[0]}'"
        raise ValueError(message)
    commit = table.get("commit", False)
    if not isinstance(commit, bool):
        message = "'commit' is true or false"
        raise ValueError(message)  # noqa: TRY004 # -- one kind for all that is wrong in a bite
    return Bite(
        gate=text(table, "gate"),
        name=text(table, "name"),
        expect=text(table, "expect"),
        fault=read_fault(table),
        args=strings(table, "args"),
        commit=commit,
        proof=text(table, "proof") if "proof" in table else "",
    )


def read_registry(source: str) -> tuple[Registry, list[str]]:
    """The registry and what is malformed in it; a malformed bite is reported and left out."""
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
    """Bites that name no gate of the binding, share a name, or a patch that is not there."""
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


def unproven(
    rows: Sequence[Row], bites: Sequence[Bite], catalogue: application_types.Catalogue
) -> Iterator[str]:
    """Each tool-bound gate with no bite, no ``remove`` for its collection, or a proof unclaimed."""
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
    """The binding's rows, the registry, what is wrong in it, and each gate it leaves unproven."""
    rows = gate_rows(gatelib.read_text(root / gatelib.BINDING) or "")
    source = gatelib.read_text(root / REGISTRY)
    if source is None:
        return rows, Registry(), ["missing; every gate bound to a tool needs a [[bite]]"], []
    registry, problems = read_registry(source)
    problems += misplaced(root, rows, registry.bites)
    gaps = list(unproven(rows, registry.bites, application_types.load()))
    return rows, registry, problems, gaps


def failures(root: Path) -> list[str]:
    """What the conformance gate reports: the registry's problems, one line each."""
    _, _, problems, gaps = inspect(root)
    return [f"{REGISTRY}: {problem}" for problem in problems + gaps]


class Shell:
    """Runs each command in a session of its own, and can stop every one still running."""

    def __init__(self) -> None:
        """Start with nothing running."""
        self.lock = threading.Lock()
        self.live: set[subprocess.Popen[bytes]] = set()
        self.stopped = False

    def run(self, command: Sequence[str], cwd: Path) -> tuple[int, str]:
        """The exit code and everything the command wrote; 127 when it cannot start.

        What the command started and left running ends with it, and the output says so. The
        output goes to a file, not a pipe, so a process left behind cannot hold the run open.
        """
        with tempfile.TemporaryFile() as log:
            with self.lock:
                if self.stopped:
                    return 130, "stopped"
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
                    return 127, f"cannot run {command[0]}: {error}"
                self.live.add(process)
            code = process.wait()
            with self.lock:
                self.live.discard(process)
            left = gatelib.end_group(process.pid)
            log.seek(0)
            output = log.read().decode("utf-8", errors="replace")
        return code, output + (f"bite: {gatelib.LEFT_RUNNING}\n" if left else "")

    def stop(self) -> None:
        """Stop every running command and refuse the next ones."""
        with self.lock:
            self.stopped = True
            live = list(self.live)
        for process in live:
            gatelib.stop_group(process)


def visible(root: Path) -> list[str]:
    """Every path git sees in the work tree: tracked or untracked, never ignored."""
    listed = git_in(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [path for path in listed.split("\0") if path]


def carry(source: Path, target: Path) -> None:
    """Copy one file or symlink, when it is there."""
    if source.is_symlink() or source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def carry_included(root: Path, copy: Path, path: str) -> None:
    """Copy one ``include`` path, a file or a directory git ignores, when it is there."""
    if not within(root, root / path):
        message = f"include '{path}' leaves the repository through a symbolic link"
        raise Unprovable(message)
    if (root / path).is_dir():
        shutil.copytree(root / path, copy / path, symlinks=True, dirs_exist_ok=True)
    carry(root / path, copy / path)


class Verdict(Enum):
    """What a gate did with a fault."""

    BITES = "bites"
    GREEN = "DOES NOT BITE"
    RED_BEFORE = "RED BEFORE THE FAULT"
    OTHER_REASON = "RED FOR ANOTHER REASON"
    NOT_PLANTED = "THE FAULT WAS NOT PLANTED"


@dataclass(frozen=True)
class Outcome:
    """One bite's verdict, and for anything but ``bites`` the reason and the gate's last lines."""

    bite: Bite
    verdict: Verdict
    detail: str = ""

    def lines(self) -> str:
        """The verdict on one line, and the detail indented under it."""
        head = f"bite: {self.bite.gate}: {self.bite.name}: {self.verdict.value}"
        return "\n".join([head, *(f"  {line}" for line in self.detail.splitlines())])


def tail(output: str) -> str:
    return "\n".join(output.splitlines()[-TAIL:])


def judge(bite: Bite, green: str, planted: tuple[int, str]) -> Outcome:
    """The verdict on a gate that printed ``green`` before the fault; ``planted`` is its run."""
    code, output = planted
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
    """One lane's scratch copy, and how it stood once its setup had run."""

    path: Path
    shell: Shell
    head: str
    staged: bytes

    def restore(self) -> None:
        """Put back every file git sees, the index and ``HEAD``; what git ignores stays."""
        snapshot = self.path / SNAPSHOT
        git_in(self.path, "reset", "-q", "--soft", self.head)
        (self.path / ".git/index").write_bytes(self.staged)
        git_in(self.path, "checkout-index", "--all", "--force", index=snapshot)
        git_in(self.path, "clean", "-ffdq", index=snapshot)

    def run(self, command: Sequence[str]) -> tuple[int, str]:
        """What the gate does to the copy as it now stands; then the copy as it stood."""
        result = self.shell.run(command, self.path)
        self.restore()
        return result

    def prove(self, row: Row, bites: Sequence[Bite]) -> Iterator[Outcome]:
        """Each bite of one gate: green without the fault, then red on it, run after run."""
        for bite in bites:
            command = [*shlex.split(row.command), *bite.args]
            # Why: a green run taken before an earlier fault says nothing of what that fault left
            # behind (a cache, a mark in an ignored file), so each fault gets its own green run.
            code, green = self.run(command)
            if code != 0:
                yield Outcome(bite, Verdict.RED_BEFORE, tail(green))
                continue
            try:
                bite.plant(self.path)
            # Why: every way a fault fails to plant is one verdict, a file the system refuses too.
            except (Unprovable, OSError) as error:
                self.restore()
                yield Outcome(bite, Verdict.NOT_PLANTED, str(error).replace(f"{self.path}/", ""))
                continue
            yield judge(bite, green, self.run(command))


def make_copy(root: Path, copy: Path, *, registry: Registry, shell: Shell) -> Copy:
    """``root`` as git sees it, as a repository of its own at ``copy``, with ``setup`` run."""

    def git_path(name: str) -> Path:
        return Path(git_in(root, "rev-parse", "--path-format=absolute", "--git-path", name))

    git_in(root, "init", "-q", str(copy))
    (copy / ".git/objects/info/alternates").write_text(f"{git_path('objects')}\n", encoding="utf-8")
    git_in(copy, "fetch", "-q", "--update-head-ok", str(root), "+refs/*:refs/*")
    head = git_in(root, "rev-parse", "HEAD")
    git_in(copy, "update-ref", "--no-deref", "HEAD", head)
    remote_head = gatelib.git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if remote_head:
        git_in(copy, "symbolic-ref", "refs/remotes/origin/HEAD", remote_head)
    for name in ("index", "info/exclude"):
        carry(git_path(name), copy / ".git" / name)
    for path in visible(root):
        carry(root / path, copy / path)
    for path in registry.include:
        carry_included(root, copy, path)
    # Why: with the default branch at `HEAD`, the base of the change is `HEAD` in the copy, so a
    # gate over the change judges the fault and not the whole branch again.
    for name in gatelib.default_branches(copy):
        ref = f"refs/remotes/{name}" if name.startswith("origin/") else f"refs/heads/{name}"
        if gatelib.git(copy, "rev-parse", "-q", "--verify", ref) is not None:
            git_in(copy, "update-ref", ref, head)
    for command in registry.setup:
        code, output = shell.run(shlex.split(command), copy)
        if code != 0:
            message = f"setup `{command}` exited {code} in the scratch copy\n{tail(output)}"
            raise Unprovable(message)
    git_in(copy, "add", "--all", index=copy / SNAPSHOT)
    return Copy(copy, shell, git_in(copy, "rev-parse", "HEAD"), (copy / ".git/index").read_bytes())


Task = tuple[Row, list[Bite]]


def run_lanes(
    root: Path, registry: Registry, tasks: Sequence[Task], *, lanes: int
) -> list[Outcome]:
    """Every task, ``lanes`` at once, each lane in a scratch copy of its own; then no copy."""
    pending: queue.SimpleQueue[Task] = queue.SimpleQueue()
    for task in tasks:
        pending.put(task)
    shell = Shell()
    printing = threading.Lock()

    def lane(path: Path, made: Copy | None) -> list[Outcome]:
        copy = made or make_copy(root, path, registry=registry, shell=shell)
        outcomes: list[Outcome] = []
        while not shell.stopped:
            try:
                row, bites = pending.get_nowait()
            except queue.Empty:
                break
            for outcome in copy.prove(row, bites):
                with printing:
                    print(outcome.lines(), flush=True)
                outcomes.append(outcome)
        return outcomes

    with (
        tempfile.TemporaryDirectory(prefix="ratchet-bite-") as scratch,
        ThreadPoolExecutor(max_workers=lanes, thread_name_prefix="bite-lane") as pool,
    ):
        paths = [Path(scratch) / f"copy-{n}" for n in range(lanes)]
        try:
            # Why: the first setup fills the caches kept outside the tree; the rest then find
            # them, where setups started together would each miss.
            first = make_copy(root, paths[0], registry=registry, shell=shell)
            # Why: a signal between a lane's start and the pool recording it would leave a lane
            # the pool does not wait for, writing into a copy that is being removed.
            with gatelib.signals_held():
                futures = [
                    pool.submit(lane, path, None if n else first) for n, path in enumerate(paths)
                ]
            # Why: the kernel may hand a signal to a lane's thread, and its handler runs only when
            # this thread runs Python, which a wait with no end would put off until the gate ends.
            while wait(futures, timeout=WAKE).not_done:
                pass
            return [outcome for future in futures for outcome in future.result()]
        except BaseException:
            shell.stop()
            raise


def proof(root: Path, registry: Registry, tasks: Sequence[Task], *, lanes: int) -> int:
    """Run the tasks and say what bit; the exit code."""
    started = time.monotonic()
    try:
        with gatelib.interrupts_raise():
            outcomes = run_lanes(root, registry, tasks, lanes=lanes)
    except Unprovable as error:
        return gatelib.report("bite", [str(error)])
    except gatelib.Interrupted as stop:
        print(f"bite: red: {stop}")
        return 128 + stop.signum
    unseen = [outcome for outcome in outcomes if outcome.verdict is not Verdict.BITES]
    print(
        f"bite: {'red' if unseen else 'green'}: {len(outcomes) - len(unseen)} of {len(outcomes)} "
        f"faults bit, over {len(tasks)} gates in {time.monotonic() - started:.0f}s"
    )
    return 1 if unseen else 0


def gate_paths_changed(root: Path) -> list[str] | None:
    """The changed paths that define a gate; ``None`` when there is no commit to compare with."""
    base = gatelib.base_commit(root)
    if base is None:
        return None
    git = guard_paths.git_in(root)
    paths = guard_paths.changed_paths(git, base)
    groups = dict(guard_paths.guarded(paths, guard_paths.manifests(git, base, root)))
    return [
        path
        for path in paths
        if gatelib.matches(path, list(GATE_PATHS))
        or (path in groups and groups[path].name in GATE_GROUPS)
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="project_bite.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--changed", action="store_true", help="only when a gate changed")
    parser.add_argument("--gate", action="append", default=[], help="only this gate's bites")
    parser.add_argument("--jobs", type=int, default=min(MAX_LANES, gatelib.default_jobs()))
    options = parser.parse_args(argv[1:])
    if os.environ.get(BITING):
        return gatelib.report("bite", ["a bite cannot plant a fault for the gate that runs it"])
    root = options.root.resolve()
    rows, registry, problems, gaps = inspect(root)
    # Why: one gate at a time is how a registry is written, so `--gate` runs before it is whole.
    problems += [] if options.gate else gaps
    if problems:
        return gatelib.report("bite", [f"{REGISTRY}: {problem}" for problem in problems])
    if options.changed and gatelib.adoption(root, gatelib.base_commit(root)):
        print(
            "bite: green: this change is the adoption, whose base holds no binding; nothing is"
            " proven here, and `bite-all` proves every gate in tier 2"
        )
        return 0
    if options.changed:
        touched = gate_paths_changed(root)
        if touched == []:
            print("bite: green: the change touches no path that defines a gate; nothing to prove")
            return 0
        print(f"bite: a gate may have changed ({', '.join(touched or ['no merge base'])})")
    tasks: list[Task] = [
        (row, [bite for bite in registry.bites if bite.gate == row.gate])
        for row in rows
        if (not options.changed or row.tier in PULL_REQUEST_TIERS)
        and (not options.gate or row.gate in options.gate)
    ]
    tasks = [task for task in tasks if task[1]]
    if not tasks:
        return gatelib.report("bite", ["no bite to run: a gate with nothing to run is red"])
    return proof(root, registry, tasks, lanes=max(1, min(options.jobs, len(tasks))))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
