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

    setup = ["just install"]             # run once in each scratch copy
    include = [".env.test"]              # ignored paths a gate needs that setup cannot rebuild

    [[bite]]
    gate = "lint"                        # the last word of the Gates row's Entry point
    name = "an unused import"
    write = { path = "src/pkg/_bite.src", content = "<an unused import>" }
    expect = "<the rule's code>"         # what the gate prints when it is red on this fault

    [[bite]]
    gate = "perf"
    name = "no benchmark"
    remove = ["tests/bench/**"]          # the empty collection
    expect = "no benchmark"

    [[bite]]
    gate = "pins"
    name = "a floating range"
    replace = { path = "<manifest>", old = "<an exact version>", new = "<a range>" }
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
behind would be judged as this fault. After a gate's last bite the gate runs again with no fault,
once for each set of ``args`` among its bites that bit, and not at all when none bit. A bite
stands only while every later run of its command with no fault is green. So ``bites`` proves
three things of one command: green on the copy as restoring leaves it, red with ``expect`` once
the fault is planted, and green again on every later run without it. It does not prove the red
came from the fault in three cases. A gate whose verdict hangs on state restoring does not put
back (what git ignores, the copy's ``.git`` beyond the index and the exclude file, anything
outside the copy, the clock) can flip between two runs on its own, and one that alternates green
and red bites without reading the fault. A gate that reads ``RATCHET_BITING`` can behave
differently in the copy than anywhere else. And ``args`` that narrow the command prove the
narrowed command, not the gate as the tier runs it. A gate with ``B`` bites runs at most ``2B``
times, plus one closing run for each set of ``args`` among the bites that bit. Every run with no
fault is one more chance for a gate that flips on its own to go red: one red one run in twenty
fails the proof of five bites about twice as often as when one green run served every bite, near
one proof in two, and those reds are named ``RED BEFORE THE FAULT`` or ``NOT PROVEN``.

One line per bite: ``bites``, ``DOES NOT BITE`` (green with the fault), ``RED BEFORE THE FAULT``
(red with no fault planted, on the copy as setup made it or as an earlier bite left it), ``RED
FOR ANOTHER REASON`` (red, and ``expect`` no more often than when green), ``THE FAULT WAS NOT
PLANTED``, ``NOT PROVEN`` (red with ``expect``, then red again with no fault planted) or ``TIMED
OUT`` (a run of the gate hit its time limit and was ended: red, and it takes the place of the
verdict its exit code alone would give; a time limit inside the gate's own tool is not seen, and
reads as ``NOT PROVEN``, ``DOES NOT BITE`` or ``RED BEFORE THE FAULT``). A run is limited to the
gate's own ``[ceilings.seconds]`` key in ``quality/thresholds.toml``, else its tier's
(``tier0``, ``tier1``, ``tier2``), else 900 seconds; ``--limit`` sets one limit for every gate
run. Each line ends with the wall time of the bite's runs and the lane they ran in, as
``(12.3s, lane 1)``, and a red one that took a second or more, and more than twice the gate's
fastest green run, says so there. A red proof ends with the machine's load at its start and at
its end. A gate's lines come once its last run ends. The run is red on anything but ``bites``, on a
registry that does not parse, on a tool-bound gate with no bite, and on one whose question
requires a proof no bite of it claims.

A scratch copy is the work tree as git sees it (tracked and untracked files, nothing ignored), as
a repository of its own under ``$TMPDIR`` with the same index and commit, borrowing the object
store read-only. ``HEAD`` is detached there and the default branch's refs point at it, so a gate
over the change sees what the tree holds beyond ``HEAD``: once the work is committed, the planted
fault and nothing else.

The real work tree is never written by the script's own writes. A fault, and everything that
restoring puts back or removes, is read and written only through descriptors of the copy's own
directories, opened one name at a time from the copy as it stood before setup ran, never through
a link. A symbolic link in the snapshot that leads outside the copy, taken after setup or
restored, is refused as unsafe (red, naming the link). So a process setup left running that swaps
a directory of the copy for a link to the real tree makes the fault ``THE FAULT WAS NOT PLANTED``
and cannot steer such a write out of the copy. Git only lists in restoring: what differs from the
snapshot and what stands beside it. Each file put back or removed goes through the descriptors, a
link at a name is unlinked and never followed, and a link where a directory belongs is replaced.
Setup code runs as the user, and nothing here stops it writing the real tree itself.

What is ignored is not copied, on purpose: a virtualenv or a build cache holds the absolute paths
of the tree it was built in, and a gate run through one would judge the real tree. ``setup``
rebuilds them in the copy from the lockfile, as a CI job does. So a gate must build what it runs,
the first run in a copy is a cold one, and whatever lives outside the tree (a port, a database, a
tool reached by an absolute path) is shared with the real one. A submodule's files are not
copied. Gates run at once, one copy per lane, never two in one copy, at most ``--jobs`` lanes:
``RATCHET_JOBS`` by default, else git's ``ratchet.jobs``, the number that caps the gates of a
tier, else the CPU count, and never more than four. The first copy is set up alone and the others
once it stands, so a cache ``setup`` fills outside the tree (a package store, a compiler cache)
is filled once. Put the build in ``setup`` as well: a gate's own build is then incremental, where a
cold one would spend a service gate's boot timeout. What a command started and left running in its
process group is ended when the command exits. A process that left the group, as ``setsid`` or a
daemon does, is not ended, and runs on, and a copy it is still writing in when the proof ends is
left behind.

Usage: ``project_bite.py [--root DIR] [--changed | --pull-request-gates] [--gate NAME]...
[--jobs N] [--limit S]``. ``--changed`` is the pull-request form: it proves nothing unless the
change touches a path that defines a gate (the manifest's ``tool-config``, ``gate-commands``,
``fitness``, ``gate-tools`` and ``formal`` groups, the registry and the binding), and then proves
the gates of tiers 0 and 1 that the change could affect (ADR 0069), one line each saying why.
The registry names a gate by each bite added, removed or changed; a patch names the gates whose
bites apply it; the binding names the gates of the rows that changed, and every gate when
anything else in it changed but the plugin pin line; a recipe in the justfile names the gates
whose command reaches it. Any other path, and a path whose reading fails, affects every gate of
tiers 0 and 1. ``--pull-request-gates`` proves every gate of tiers 0 and 1 with no change to
read, to measure what the pull-request form costs; it cannot be combined with ``--changed``. The
proof proves nothing on the adoption either, the change whose base holds no binding: every
gate is new there, and the whole proof runs once in tier 2.
"""

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
import justfile_dump
import plugin_pin

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
FRESH = ".git/bite-fresh-index"
# Why: a chain of links longer than this is a loop, or as good as one, and is refused.
MAX_LINK_HOPS = 40
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
MIN_CELLS = 4  # Why: Question, Tool, Tier, Threshold source, Entry point.
GATE_LIMIT = 900.0
SLOW = 2.0
SLOW_FLOOR = 1.0
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
    limit: float = GATE_LIMIT
    line: str = field(default="", compare=False)

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
    return list(split_gates(binding)[0])


@functools.lru_cache(maxsize=256)
def split_gates(binding: str) -> tuple[tuple[Row, ...], str]:
    """The Gates table's rows, and the binding with those rows taken out."""
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
        elif len(cells) > MIN_CELLS:
            command = cells[4].partition("`")[2].partition("`")[0]
            rows.append(Row(cells[0], cells[1], cells[2], command, line="|".join(cells)))
        else:
            kept.append(line)
    return tuple(rows), before + marker + "".join(kept) + divider + after


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

    def clear(self, path: str) -> None:
        """Delete what stands at ``path``, a directory with all it holds; a link is not followed."""
        with self.at(path.rstrip("/")) as (here, name):
            self.empty(here, name)

    def put(self, path: str, mode: str, content: bytes) -> None:
        """What the index holds at ``path``, replacing whatever stands there."""
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
        """Delete ``name`` in the open directory ``here``, whatever it is; nothing is followed."""
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


class Run(NamedTuple):
    """One command's end: exit code, output, wall time, and whether the limit ended it."""

    code: int
    output: str
    seconds: float = 0.0
    timed_out: bool = False


class Shell:
    """Runs each command in a session of its own, and can stop every one still running."""

    def __init__(self) -> None:
        """Start with nothing running."""
        self.lock = threading.Lock()
        self.live: set[subprocess.Popen[bytes]] = set()
        self.stopped = False

    def run(self, command: Sequence[str], cwd: Path, limit: float | None = None) -> Run:
        """The exit code and everything the command wrote; 127 when it cannot start.

        A command still running after ``limit`` seconds is ended with its group and the run says so.

        What the command started and left running in its process group ends with it, and the
        output says so; a process that left the group runs on. The output goes to a file, not a
        pipe, so a process left behind cannot hold the run open.
        """
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
    TIMED_OUT = "TIMED OUT"


@dataclass(frozen=True)
class Outcome:
    """One bite's verdict, and for anything but ``bites`` the reason and the gate's last lines."""

    bite: Bite
    verdict: Verdict
    detail: str = ""
    seconds: float = 0.0
    lane: int = 0
    red_seconds: float = 0.0
    slower: float = 0.0

    def lines(self) -> str:
        """The verdict, its wall time and lane on one line, and the detail indented under it."""
        note = f"; {self.slower:.1f}x this gate's fastest green run" if self.slower > SLOW else ""
        head = (
            f"bite: {self.bite.gate}: {self.bite.name}: {self.verdict.value}"
            f" ({self.seconds:.1f}s, lane {self.lane}{note})"
        )
        return "\n".join([head, *(f"  {line}" for line in self.detail.splitlines())])

    def unproven(self, later: Run) -> Outcome:
        """This bite, not proven: a later run with no fault planted was red; timed out, say so."""
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
        """This bite, ended by the time limit: red, whatever its exit code alone would say."""
        return replace(self, verdict=Verdict.TIMED_OUT, detail=tail(output))


def tail(output: str) -> str:
    return "\n".join(output.splitlines()[-TAIL:])


def judge(bite: Bite, green: str, planted: Run) -> Outcome:
    """The verdict on a gate that printed ``green`` before the fault; ``planted`` is its run."""
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
    """One lane's scratch copy, and how it stood once its setup had run."""

    tree: Tree
    shell: Shell
    head: str
    snapshot_tree: str
    staged: bytes
    exclude: bytes
    lane: int = 0

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
        # Why: git only lists here; every delete and write goes through a descriptor of the copy.
        # A fresh index holds no stat data, so the refresh compares every file's content and
        # a gate that kept a file's size and times cannot hide its change.
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
        # Why: defence in depth; the snapshot was checked, and what it now writes is checked again.
        contained(self.path, self.head)

    def run(self, command: Sequence[str], limit: float) -> Run:
        """What the gate does to the copy as it now stands; then the copy as it stood."""
        result = self.shell.run(command, self.path, limit)
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
        greens: list[float] = []
        for bite in bites:
            if self.shell.stopped:
                return
            # Why: a green run taken before an earlier fault says nothing of what that fault left
            # behind (a cache, a mark in an ignored file), so each fault gets its own green run.
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
            # Why: every way a fault fails to plant is one verdict, a file the system refuses too.
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
        # Why: a gate red from its second run on, by state it keeps, is red on every fault.
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
        """A verdict of this bite, timed, in this copy's lane."""
        return Outcome(bite, verdict, detail, seconds, self.lane, red)

    def unfaulted(
        self, row: Row, args: tuple[str, ...], outcomes: list[Outcome], *, greens: list[float]
    ) -> Run:
        """A run with no fault planted; red, it leaves no bite before it with ``args`` standing.

        A green run's seconds join ``greens``.
        """
        result = self.run([*shlex.split(row.command), *args], row.limit)
        if result.code == 0:
            greens.append(result.seconds)
        else:
            outcomes[:] = [
                o.unproven(result) if o.verdict is Verdict.BITES and o.bite.args == args else o
                for o in outcomes
            ]
        return result


def make_copy(root: Path, copy: Path, *, registry: Registry, shell: Shell, lane: int = 0) -> Copy:
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
    contained(copy, git_in(copy, "rev-parse", "HEAD"))


def under(absolute: str, bases: tuple[str, ...]) -> list[str] | None:
    """The parts of ``absolute`` below the first of ``bases`` holding it, or ``None``.

    The target is taken as written: its ``..`` are walked with the links on the way by the
    caller, never collapsed here, where ``L/../x`` would be ``x`` whatever ``L`` leads to.
    """
    for base in bases:
        if absolute == base or absolute.startswith(base + os.sep):
            return absolute[len(base) :].split("/")
    return None


def ends_inside(path: str, targets: dict[str, str], bases: tuple[str, ...], *, copy: Path) -> bool:
    """Whether the link ``path`` ends inside the copy, by the link texts ``targets`` hold.

    Judged from the text the snapshot holds, which is what restoring writes, never from the copy
    as it stands: setup left running can put a directory back by the time this looks, and a link
    accepted that way is written again, live, by the next restore.
    """
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
            # Why: a link git ignores is not in the snapshot and is not restored, but it stays in
            # the copy, and a link to it still leads where it leads; so it is followed as it is.
            # Such a link is not itself refused: a fault is refused through any link (``owned``),
            # restoring replaces a link and never follows one, and what setup fills outside the
            # tree through one (a package store) is what ``setup`` is for.
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
    """Refuse a link in the snapshot that resolves outside the copy; ``Unprovable`` names it.

    A link to the real tree, restored, lets a later path operation in the copy write there. A
    link is judged by where it ends, absolute or relative, through every link on the way. A link
    the commit already holds, unchanged, is the repository's own (faults through it are refused
    by ``owned``); one setup made or changed is not.
    """

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


def gate_limit(thresholds: str, row: Row, override: float | None) -> float:
    """Seconds one run of the gate may take: ``--limit``, else its own ceiling, else its tier's."""
    if override is not None:
        return override
    for key in (row.gate, f"tier{row.tier}"):
        ceiling = budget.ceiling_of(thresholds, key)
        if ceiling is not None:
            return ceiling
    return GATE_LIMIT


def run_lanes(
    root: Path, registry: Registry, tasks: Sequence[Task], *, lanes: int, limit: float | None
) -> list[Outcome]:
    """Every task, ``lanes`` at once, each lane in a scratch copy of its own; then no copy."""
    pending: queue.SimpleQueue[Task] = queue.SimpleQueue()
    file = root / gatelib.THRESHOLDS
    thresholds = file.read_text(encoding="utf-8") if file.is_file() else ""
    for row, bites in tasks:
        pending.put((replace(row, limit=gate_limit(thresholds, row, limit)), bites))
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
                    pool.submit(lane, n, path, None if n else first) for n, path in enumerate(paths)
                ]
            # Why: the kernel may hand a signal to a lane's thread, and its handler runs only when
            # this thread runs Python, which a wait with no end would put off until the gate ends.
            while wait(futures, timeout=WAKE).not_done:
                pass
            return [outcome for future in futures for outcome in future.result()]
        except BaseException:
            shell.stop()
            raise


def load() -> str:
    """The machine's one-minute load average and core count, or ``unknown`` where it has none."""
    average = getattr(os, "getloadavg", None)
    if average is None:
        return "unknown"
    return f"{average()[0]:.1f} on {os.cpu_count()} cores"


def proof(
    root: Path, registry: Registry, tasks: Sequence[Task], *, lanes: int, limit: float | None
) -> int:
    """Run the tasks and say what bit; the exit code."""
    started = time.monotonic()
    load_before = load()
    print(
        f"bite: limit: {limit:g}s a run"
        if limit is not None
        else f"bite: limit: each gate's ceiling, else its tier's, else {GATE_LIMIT:g}s a run"
    )
    try:
        with gatelib.interrupts_raise():
            outcomes = run_lanes(root, registry, tasks, lanes=lanes, limit=limit)
    # Why: a copy no walk can reach, or a system that refuses one, is a run that proves nothing.
    except (Unprovable, OSError) as error:
        return gatelib.report("bite", [str(error)])
    except gatelib.Interrupted as stop:
        print(f"bite: red: {stop}")
        return 128 + stop.signum
    unseen = [outcome for outcome in outcomes if outcome.verdict is not Verdict.BITES]
    print(
        f"bite: {'red' if unseen else 'green'}: {len(outcomes) - len(unseen)} of {len(outcomes)} "
        f"faults bit, over {len(tasks)} gates in {time.monotonic() - started:.0f}s"
    )
    if unseen:
        print(f"bite: load: {load_before} at the start, {load()} at the end")
    return 1 if unseen else 0


class Touched(NamedTuple):
    """A changed path that defines a gate, and the manifest group that says so (none: ``""``)."""

    path: str
    group: str


Cause = justfile_dump.Cause
JUSTFILE_NAMES = ("justfile", ".justfile", "Justfile")
READER = {"gate-commands": "runs it"}
READS = "## What each gate reads"
READS_BULLET = re.compile(r"^- ([^:\s]+):[ \t]*(.*)$")


def gate_paths_changed(root: Path, base: str | None) -> list[Touched] | None:
    """The changed paths that define a gate; ``None`` when there is no commit to compare with."""
    if base is None:
        return None
    git = guard_paths.git_in(root)
    paths = guard_paths.changed_paths(git, base)
    groups = dict(guard_paths.guarded(paths, guard_paths.manifests(git, base, root)))
    touched = []
    for path in paths:
        if gatelib.matches(path, list(GATE_PATHS)):
            touched.append(Touched(path, ""))
        elif path in groups and groups[path].name in GATE_GROUPS:
            touched.append(Touched(path, groups[path].name))
    return touched


def registries(root: Path, base: str) -> tuple[Registry | None, Registry | None]:
    """The registry at the merge base and at head; ``None`` for one that is absent or malformed."""
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
    return [Cause(gate, f"patch {path} is named by bite `{name}`") for gate, name in sorted(named)]


def binding_causes(root: Path, base: str) -> list[Cause]:
    binding = str(gatelib.BINDING)
    old, new = gatelib.show(root, base, binding), gatelib.read_text(root / binding)
    if old is None or new is None:
        return [Cause(None, f"{binding} was added or removed")]
    (old_rows, old_rest), (new_rows, new_rest) = split_gates(old), split_gates(new)
    if plugin_pin.PIN_LINE.sub("", old_rest) != plugin_pin.PIN_LINE.sub("", new_rest):
        return [Cause(None, f"{binding} changed outside its gate rows, which a gate may read")]
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
        causes += [
            Cause(row.gate, f"the binding's row `{question}` {verb}")
            for row in (was, now)
            if row is not None and row.gate
        ]
    return causes


def justfile_causes(root: Path, base: str, path: str) -> list[Cause]:
    binding = str(gatelib.BINDING)
    commands: dict[str, str] = {}
    for text in (gatelib.show(root, base, binding), gatelib.read_text(root / binding)):
        commands |= {row.gate: row.command for row in gate_rows(text or "") if row.gate}
    return justfile_dump.causes(
        path, gatelib.show(root, base, path), gatelib.read_text(root / path), commands
    )


def gate_reads(binding: str) -> dict[str, list[str]]:
    """Each bullet of ``## What each gate reads``: the gate, and the paths it reads."""
    _, marker, body = binding.partition(f"\n{READS}\n")
    reads: dict[str, list[str]] = {}
    for line in body.partition("\n## ")[0].splitlines() if marker else []:
        found = READS_BULLET.match(line)
        if found:
            entries = [entry.strip().strip("`") for entry in found[2].split(",")]
            reads.setdefault(found[1], []).extend(entry for entry in entries if entry)
    return reads


def reading(path: str, entry: str) -> bool:
    """Whether a bullet's file, directory or glob covers a repository path."""
    return gatelib.matches(path, [entry, f"{entry.rstrip('/')}/**"])


def unnamed_causes(root: Path, path: str, group: str) -> list[Cause]:
    """The gates a path no gate's row names could affect.

    A tool-config path goes to the gates whose bullet reads it and those with no bullet. Any other
    path, and a tool-config path with no bullet in the binding at all, goes to every gate.
    """
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    reads = gate_reads(binding) if group == "tool-config" else {}
    if not reads:
        who = READER.get(group, "reads it")
        return [Cause(None, f"{path} changed, and no record says which gate {who}")]
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
    """Every gate the change could make unable to fail, each with why; no gate is every gate.

    One source per kind of path. Where nothing records which gate a path serves, the cause names
    no gate.
    """
    if base is None or touched is None:
        return [Cause(None, "there is no merge base to compare with")]
    causes: list[Cause] = []
    for path, group in touched:
        if path == str(REGISTRY):
            causes += registry_causes(root, base)
        elif gatelib.matches(path, [f"{PATCHES}**"]):
            causes += patch_causes(root, base, path)
        elif path == str(gatelib.BINDING):
            causes += binding_causes(root, base)
        elif group == "gate-commands" and path in JUSTFILE_NAMES:
            causes += justfile_causes(root, base, path)
        else:
            causes += unnamed_causes(root, path, group)
    return causes


def pull_request_gates(rows: Sequence[Row]) -> list[Row]:
    """Every gate of tiers 0 and 1."""
    return [row for row in rows if row.tier in PULL_REQUEST_TIERS]


def fallback(rows: Sequence[Row]) -> list[Row]:
    """The gates to prove when a cause names no gate: every gate of tiers 0 and 1, for now."""
    return pull_request_gates(rows)


def targeted(rows: Sequence[Row], causes: Sequence[Cause]) -> list[Row]:
    """The gates the causes put in the set, and the one place that falls back."""
    if any(cause.gate is None for cause in causes):
        return fallback(rows)
    named = {cause.gate for cause in causes}
    return [row for row in pull_request_gates(rows) if row.gate in named]


def explain(rows: Sequence[Row], causes: Sequence[Cause]) -> list[str]:
    """One line for each gate in the set; on a fallback one line, which ``budget.py`` reads."""
    if any(cause.gate is None for cause in causes):
        whys = dict.fromkeys(cause.why for cause in causes if cause.gate is None)
        return [f"{budget.FALLBACK}{'; '.join(whys)}; proving every gate"]
    return [
        f"bite: {row.gate}: "
        + "; ".join(dict.fromkeys(cause.why for cause in causes if cause.gate == row.gate))
        for row in targeted(rows, causes)
    ]


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
    # Why: one gate at a time is how a registry is written, so `--gate` runs before it is whole.
    problems += [] if options.gate else gaps
    if problems:
        return gatelib.report("bite", [f"{REGISTRY}: {problem}" for problem in problems])
    chosen = pull_request_gates(rows) if options.pull_request_gates else rows
    if options.changed:
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
        if not chosen:
            print("bite: green: the change affects no gate of tiers 0 and 1; nothing to prove")
            return 0
        print("\n".join(explain(rows, causes)))
    tasks: list[Task] = [
        (row, [bite for bite in registry.bites if bite.gate == row.gate])
        for row in chosen
        if not options.gate or row.gate in options.gate
    ]
    tasks = [task for task in tasks if task[1]]
    if not tasks:
        return gatelib.report("bite", ["no bite to run: a gate with nothing to run is red"])
    lanes = max(1, min(options.jobs, len(tasks)))
    return proof(root, registry, tasks, lanes=lanes, limit=options.limit)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
