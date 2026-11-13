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
``THE FAULT WAS NOT PLANTED``, which is red. So is a fault, of any kind, aimed where restoring
the copy does not reach: a file git ignores, ``.git``, a path through a symbolic link, a path
inside a submodule or another repository setup made, or a new ``.gitignore`` or
``.gitattributes``, the rules restoring works by. A fault there would stay planted for the next
bite. A file a fault changes is written anew, so another name for the same data (a hard link)
keeps what it held. ``args`` are appended to the gate's command, and ``commit = true`` commits
the fault in the copy first, for a gate that reads history or the index.

Once setup has run, the copy is recorded: every file git sees or tracks (one an ignore rule
also matches included), the index, ``HEAD`` and ``.git/info/exclude``. Restoring the copy puts
all of that back and removes every other file git sees; what git ignores stays as the last run
left it. The copy is restored after every run and once before the first, so every run of a gate,
green or with a fault, starts from the copy as restoring leaves it. What restoring takes away is
absent from every run: an empty directory setup made is gone, since git holds no directory.

For each bite the script runs the gate and requires green, plants the fault, runs the gate again,
and requires red with ``expect`` in its output more often than in that green run (a recipe's own
text is echoed on every run). No green run serves two bites: what an earlier fault's run left
behind would be judged as this fault. After a gate's last bite the gate runs once more with no
fault, once for each set of ``args`` its bites that bit use. A bite stands only while every later
run of its command with no fault is green. So ``bites`` proves three things of one command: green
on the copy as restoring leaves it, red with ``expect`` once the fault is planted, and green again
on every later run without it. It does not prove the red came from the fault when the gate's
verdict hangs on state restoring does not put back (what git ignores, the copy's ``.git`` beyond
the index and the exclude file, anything outside the copy, the clock): such a gate can still flip
between two runs on its own, and one that alternates green and red bites without reading the
fault. The closing run costs one run per gate (per set of ``args``): a gate with ``B`` bites runs
``2B + 1`` times.

One line per bite: ``bites``, ``DOES NOT BITE`` (green with the fault), ``RED BEFORE THE FAULT``
(red with no fault planted, on the copy as setup made it or as an earlier bite left it), ``RED
FOR ANOTHER REASON`` (red, and ``expect`` no more often than when green), ``THE FAULT WAS NOT
PLANTED`` or ``NOT PROVEN`` (red with ``expect``, then red again with no fault planted). A
gate's lines come once its last run ends. The run is red on anything but ``bites``, on a
registry that does not parse, on a tool-bound gate with no bite, and on one whose question
requires a proof no bite of it claims.

A scratch copy is the work tree as git sees it (tracked and untracked files, nothing ignored), as
a repository of its own under ``$TMPDIR`` with the same index and commit, borrowing the object
store read-only. ``HEAD`` is detached there and the default branch's refs point at it, so a gate
over the change sees what the tree holds beyond ``HEAD``: once the work is committed, the planted
fault and nothing else.

The real work tree is never written by the script's own writes. A fault, and the index and
exclude file that restoring puts back, are read and written only through descriptors of the
copy's own directories, opened one name at a time from the copy as it stood before setup ran,
never through a link. So a process setup left running that swaps a directory of the copy for a
link to the real tree makes the fault ``THE FAULT WAS NOT PLANTED`` and cannot steer such a write
out of the copy. The rest of restoring is git's (``checkout-index``, ``clean``): git checks each
path for a link before it writes there, and then writes by path. Setup code runs as the user, and
nothing here stops it writing the real tree itself.

What is ignored is not copied, on purpose: a virtualenv or a build cache holds the absolute paths
of the tree it was built in, and a gate run through one would judge the real tree. ``setup`` rebuilds them in the copy from the
lockfile, as a CI job does. So a gate must build what it runs, the first run in a copy is a cold
one, and whatever lives outside the tree (a port, a database, a tool reached by an absolute path)
is shared with the real one. A submodule's files are not copied. Gates run at once, one copy per
lane, never two in one copy, at most ``--jobs`` lanes: ``RATCHET_JOBS`` by default, the number
that caps the gates of a tier, else the CPU count, and never more than four. The first copy is
set up alone and the others once it stands, so a cache ``setup`` fills outside the tree (a
package store, a compiler cache) is filled once. Put
the build in ``setup`` as well: a gate's own build is then incremental, where a cold one would
spend a service gate's boot timeout. What a command started and left running in its process
group is ended when the command exits. A process that left the group, as ``setsid`` or a daemon
does, is not ended, and runs on.

Usage: ``project_bite.py [--root DIR] [--changed] [--gate NAME]... [--jobs N]``. ``--changed`` is
the pull-request form: it proves nothing unless the change touches a path that defines a gate
(the manifest's ``tool-config``, ``gate-commands``, ``fitness``, ``gate-tools`` and ``formal``
groups, the registry and the binding), and then proves the gates of tiers 0 and 1. It proves
nothing on the adoption either, the change whose base holds no binding: every gate is new
there, and the whole proof runs once in tier 2.
"""

from __future__ import annotations

import argparse
import contextlib
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
MISSING = "missing; every gate bound to a tool needs a [[bite]]"
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
# Why: a gate that adds a rule here hides from the restore what it, or a fault, left behind.
EXCLUDE = ".git/info/exclude"
INDEX = ".git/index"
# Why: restoring the copy cleans by the one and writes files by the other, so a new one planted
# would decide how the copy it planted in is put back.
RULES = (".gitignore", ".gitattributes")
CHECK = ".git/bite-check-index"
# A file's mode in the index, as the mode a fault writes it with.
FILE_MODES = {"100644": 0o644, "100755": 0o755}
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW
# Why: without these a directory is opened only by following whatever link its name holds.
DESCRIPTORS = (
    bool(NOFOLLOW)
    and DIRECTORY != os.O_RDONLY | NOFOLLOW
    and {os.open, os.mkdir, os.rmdir, os.stat, os.unlink} <= os.supports_dir_fd
    and os.listdir in os.supports_fd
)
UNSUPPORTED = (
    "this system cannot open a directory without following a link, so the scratch copy is"
    " neither written nor put back"
)
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


def run_git(
    root: Path, *args: str, index: Path | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    """``git -C root args``, judging ``root`` and no other tree, through ``index`` when given."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
        input=None if stdin is None else stdin.encode(),
        env=environment() | ({"GIT_INDEX_FILE": str(index)} if index else {}),
    )


def git_bytes(root: Path, *args: str, index: Path | None = None, stdin: str | None = None) -> bytes:
    """The stdout of ``run_git``; raises ``Unprovable`` when git fails."""
    result = run_git(root, *args, index=index, stdin=stdin)
    if result.returncode != 0:
        message = f"git {' '.join(args)} failed in {root}: {result.stderr.decode().strip()}"
        raise Unprovable(message)
    return result.stdout


def git_in(root: Path, *args: str, index: Path | None = None, stdin: str | None = None) -> str:
    """The stdout of ``run_git`` as text; raises ``Unprovable`` when git fails."""
    return git_bytes(root, *args, index=index, stdin=stdin).decode().strip()


def environment() -> dict[str, str]:
    kept = {key: value for key, value in os.environ.items() if key not in REAL_TREE}
    return {**kept, BITING: "1"}


@dataclass(frozen=True)
class Write:
    """Create or replace one file."""

    path: str
    content: str

    def plant(self, copy: Tree) -> None:
        """Write the file, as a change git sees."""
        owned(copy.path, self.path)
        copy.write(self.path, self.content.encode(), create=creatable(self.path))
        git_in(copy.path, "add", "--intent-to-add", "--", self.path)


@dataclass(frozen=True)
class Patch:
    """Apply a unified diff kept under ``quality/bites/``."""

    path: str

    def plant(self, copy: Tree) -> None:
        """Apply the diff; a new file in it is a change git sees."""
        # Why: `git apply` writes any file of the work tree, ignored ones too. With `--index` it
        # finds every file the diff reads in the copy as it stood, which holds only what git sees.
        # It writes the work tree by path, so the diff goes to an index and the files from there.
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
    """Delete every file a glob matches: the collection a gate judges, emptied."""

    patterns: tuple[str, ...]

    def plant(self, copy: Tree) -> None:
        """Delete the files; ``Unprovable`` when the globs match none."""
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
        # Why: git holds no empty directory, so a collection with no file has no directory either.
        parents = {str(PurePosixPath(path).parent) for path in files} - {"."}
        for directory in sorted(parents, reverse=True):
            copy.prune(directory)


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

    def plant(self, copy: Tree) -> None:
        """Replace each text; ``Unprovable`` when one stands nowhere or in more than one place."""
        for swap in self.swaps:
            owned(copy.path, swap.path)
            content, old = copy.read(swap.path), swap.old.encode()
            if content is None:
                message = f"{swap.path} is not a file in the copy, and 'replace' changes a file"
                raise Unprovable(message)
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
            copy.write(swap.path, content[:first] + swap.new.encode() + content[first + len(old) :])


Fault = Write | Patch | Remove | Replace


def owned(copy: Path, *paths: str) -> None:
    """Whether a fault may change each path in the copy: one restoring the copy puts back.

    Restoring puts back what the snapshot holds and removes what git sees beside it, so a fault
    anywhere else would stay planted for the runs after it: through a link, inside another
    repository, or in a file git ignores. ``Unprovable`` says why the first such path is not one.
    A link is refused here before git is asked about a path beyond one, and again where the
    fault is written (``Tree``), which holds whatever changed in between.
    """
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
    # Why: a submodule's files are not copied; a repository setup made is in the copy.
    for path in paths:
        for name in above(path):
            if name in gitlinks or (copy / name / ".git").exists():
                message = (
                    f"{path} is inside {name}, a repository of its own, whose files restoring"
                    " the copy does not put back"
                )
                raise Unprovable(message)
    # Why: judged by the snapshot, a file it holds is tracked even where an ignore rule matches.
    # 1 is git's answer when it ignores none of the paths; 0 names those it ignores, more is none.
    ignored = run_git(
        copy, "check-ignore", "-z", "--stdin", index=copy / SNAPSHOT, stdin="\0".join(paths)
    )
    if ignored.returncode != 1:
        named = ignored.stdout.decode().partition("\0")[0] or " or ".join(paths)
        message = f"{named} is ignored by git, and restoring the copy puts back only what git sees"
        raise Unprovable(message)


def above(path: str) -> list[str]:
    """The directories ``path`` lies in, the outermost first."""
    parts = PurePosixPath(path).parts
    return ["/".join(parts[:depth]) for depth in range(1, len(parts))]


def staged(copy: Path, index: Path) -> dict[str, tuple[str, str]]:
    """Each path ``index`` holds, with its mode and blob."""
    listed = git_in(copy, "ls-files", "-z", "--stage", index=index).split("\0")
    entries = [line.partition("\t") for line in listed if line]
    return {path: (entry.split()[0], entry.split()[1]) for entry, _, path in entries}


def creatable(path: str) -> bool:
    """Whether a fault may make ``path`` where no file stands: any file but a rules file."""
    return PurePosixPath(path).name not in RULES


def linked(path: str) -> str:
    return f"{path} is reached through a symbolic link; a fault changes the copy's own file"


@dataclass(frozen=True)
class Tree:
    """The scratch copy, read and written only through descriptors of its own directories.

    What setup leaves running can swap a directory of the copy for a link to the real tree
    between a check and a write. Each directory on a path is opened from the one before it,
    starting from the copy as it was before setup ran, and never through a link: a link swapped
    in anywhere on the way fails the open, and a directory once open stays the copy's own,
    whatever its name comes to point at. No read or write here follows a link.
    """

    path: Path
    made: tuple[int, int]

    @classmethod
    def pin(cls, path: Path) -> Tree:
        """The directory at ``path``, as the one every later walk must start from."""
        found = path.lstat()
        return cls(path, (found.st_dev, found.st_ino))

    @contextmanager
    def at(self, path: str, *, make: bool = False) -> Iterator[tuple[int, str]]:
        """The open directory ``path`` lies in, and its last name; ``make`` makes the way."""
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
        """What the file at ``path`` holds; ``None`` when no file is there."""
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
        """``content`` as a new file at ``path``, with ``mode`` or else the mode the file had.

        A file written in place would change every other name for it too (a hard link setup
        made), and restoring the copy gives back only the name git sees. With ``create`` false,
        a path where no file stands is refused.
        """
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
        """Delete the file at ``path``."""
        with self.at(path) as (here, name):
            old = entry(here, name)
            if old is not None and stat.S_ISLNK(old.st_mode):
                raise Unprovable(linked(path))
            os.unlink(name, dir_fd=here)

    def prune(self, path: str) -> None:
        """Delete the directory at ``path`` when it holds nothing."""
        with self.at(path) as (here, name):
            opened = os.open(name, DIRECTORY, dir_fd=here)
            try:
                empty = not os.listdir(opened)  # noqa: PTH208 # -- a descriptor, not a path
            finally:
                os.close(opened)
            if empty:
                os.rmdir(name, dir_fd=here)


def entry(here: int, name: str) -> os.stat_result | None:
    """``name`` in the open directory ``here``, not followed; ``None`` when nothing is there."""
    try:
        return os.stat(name, dir_fd=here, follow_symlinks=False)
    except FileNotFoundError:
        return None


def refusal(error: OSError, path: str, *, at: tuple[int, str], where: str) -> Exception:
    """Why ``path`` was not reached: the name ``at`` is a link, or what the system said."""
    found = entry(*at)
    if found is not None and stat.S_ISLNK(found.st_mode):
        return Unprovable(linked(path))
    return OSError(error.errno, error.strerror, where)


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

    def plant(self, copy: Tree) -> None:
        """Plant the fault in ``copy`` as a change git sees, committed when the bite says so."""
        self.fault.plant(copy)
        if self.commit:
            git_in(copy.path, "add", "-A")
            git_in(copy.path, *COMMIT, f"test(bite): {self.name}")


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
    registry, problems = read_registry(source) if source is not None else (Registry(), [MISSING])
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

        What the command started and left running in its process group ends with it, and the
        output says so; a process that left the group runs on. The output goes to a file, not a
        pipe, so a process left behind cannot hold the run open.
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
    NOT_PROVEN = "NOT PROVEN"


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

    def unproven(self, red: str) -> Outcome:
        """This bite, not proven: a later run with no fault planted printed ``red`` and was red."""
        why = (
            "the gate did not return to green once the fault was gone, so its red may come from"
            " what its own runs leave and not from the fault"
        )
        return Outcome(self.bite, Verdict.NOT_PROVEN, f"{why}\n{tail(red)}")


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

    tree: Tree
    shell: Shell
    head: str
    staged: bytes
    exclude: bytes

    @property
    def path(self) -> Path:
        """Where the copy is."""
        return self.tree.path

    def restore(self) -> None:
        """Put back what the snapshot holds, the index, ``HEAD`` and the exclude file.

        What git sees beside the snapshot is removed. What git ignores stays.
        """
        snapshot = self.path / SNAPSHOT
        git_in(self.path, "reset", "-q", "--soft", self.head)
        self.tree.write(INDEX, self.staged)
        self.tree.write(EXCLUDE, self.exclude)
        git_in(self.path, "checkout-index", "--all", "--force", index=snapshot)
        git_in(self.path, "clean", "-ffdq", index=snapshot)

    def run(self, command: Sequence[str]) -> tuple[int, str]:
        """What the gate does to the copy as it now stands; then the copy as it stood."""
        result = self.shell.run(command, self.path)
        self.restore()
        return result

    def prove(self, row: Row, bites: Sequence[Bite]) -> Iterator[Outcome]:
        """Each bite of one gate: green without the fault, red on it, and green again after.

        A bite stands only while every later run of its command with no fault planted is green:
        the green run of each later bite with the same ``args``, and one closing run after the
        last bite, for each set of ``args`` a bite that bit gave the gate. Once the proof is
        stopped, nothing more runs and no verdict is given.
        """
        outcomes: list[Outcome] = []
        for bite in bites:
            if self.shell.stopped:
                return
            # Why: a green run taken before an earlier fault says nothing of what that fault left
            # behind (a cache, a mark in an ignored file), so each fault gets its own green run.
            code, green = self.unfaulted(row, bite.args, outcomes)
            if code != 0:
                outcomes.append(Outcome(bite, Verdict.RED_BEFORE, tail(green)))
                continue
            try:
                bite.plant(self.tree)
            # Why: every way a fault fails to plant is one verdict, a file the system refuses too.
            except (Unprovable, OSError) as error:
                self.restore()
                why = str(error).replace(f"{self.path}/", "")
                outcomes.append(Outcome(bite, Verdict.NOT_PLANTED, why))
                continue
            outcomes.append(judge(bite, green, self.run([*shlex.split(row.command), *bite.args])))
        # Why: a gate red from its second run on, by state it keeps, is red on every fault.
        for args in dict.fromkeys(o.bite.args for o in outcomes if o.verdict is Verdict.BITES):
            self.unfaulted(row, args, outcomes)
        if not self.shell.stopped:
            yield from outcomes

    def unfaulted(
        self, row: Row, args: tuple[str, ...], outcomes: list[Outcome]
    ) -> tuple[int, str]:
        """A run with no fault planted; red, it leaves no bite before it with ``args`` standing."""
        code, output = self.run([*shlex.split(row.command), *args])
        if code != 0:
            outcomes[:] = [
                o.unproven(output) if o.verdict is Verdict.BITES and o.bite.args == args else o
                for o in outcomes
            ]
        return code, output


def make_copy(root: Path, copy: Path, *, registry: Registry, shell: Shell) -> Copy:
    """``root`` as git sees it, as a repository of its own at ``copy``, with ``setup`` run."""

    def git_path(name: str) -> Path:
        return Path(git_in(root, "rev-parse", "--path-format=absolute", "--git-path", name))

    git_in(root, "init", "-q", str(copy))
    # Why: pinned before setup runs, so nothing setup leaves running decides what the copy is.
    tree = Tree.pin(copy)
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
    snapshot(copy)
    made = Copy(
        tree,
        shell,
        git_in(copy, "rev-parse", "HEAD"),
        tree.read(INDEX) or b"",
        tree.read(EXCLUDE) or b"",
    )
    # Why: every run then starts from the copy as restoring leaves it, the first green run too;
    # where setup left what restoring takes away (an empty directory), every run judges without it.
    made.restore()
    return made


def snapshot(copy: Path) -> None:
    """The copy as it now stands, as the index ``SNAPSHOT``: every file git sees or tracks."""
    git_in(copy, "add", "--all", index=copy / SNAPSHOT)
    # Why: `add --all` leaves out a file the index tracks where an ignore rule matches it too.
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
        # Why: a process setup left running may still be writing in a copy as it is removed.
        tempfile.TemporaryDirectory(prefix="ratchet-bite-", ignore_cleanup_errors=True) as scratch,
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
