"""Fakes and fixtures shared by the guard tests: a fake filesystem and git, a fixture manifest."""

import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ratchet_guards import bash
from ratchet_guards.context import Context, Guard
from ratchet_guards.globs import matches
from ratchet_guards.manifest import Manifest, parse, with_plugin_group
from ratchet_guards.paths import Checkout, Locator
from ratchet_guards.records import Before
from ratchet_guards.refs import MAIN_BRANCHES
from ratchet_guards.verdict import Verdict

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_TEXT = (Path(__file__).resolve().parent / "fixtures/manifest.toml").read_text(
    encoding="utf-8"
)
MANIFEST = parse(MANIFEST_TEXT)
STANDARD = parse(
    (PLUGIN_ROOT / "skills/adopt/templates/guards/manifest.toml").read_text(encoding="utf-8")
)
PLUGIN = Path("/plugin")
ROOT = Path("/repo")
WORKTREE = ROOT / ".claude/worktrees/agent-1"
UNGUARDED = Path("/elsewhere")
HOME = Path("/home/agent")
LABELS_RECORD = """# Triage Labels

| Label in mattpocock/skills | Label in our tracker | Meaning |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | Maintainer needs to evaluate this issue |
| `ready-for-agent` | `agent:ready` | Fully specified, ready for an AFK agent |
| `wontfix` | `wontfix` | Will not be actioned |
"""
# What git reads each name as in the fake repository: a clone whose ``main`` and ``master``
# track the remote's, with the local branches the corpus switches between.
NAMES = {
    "origin/main": "refs/remotes/origin/main",
    "origin/master": "refs/remotes/origin/master",
    "refs/remotes/origin/main": "refs/remotes/origin/main",
    "refs/remotes/origin/master": "refs/remotes/origin/master",
    "main@{upstream}": "refs/remotes/origin/main",
    "master@{upstream}": "refs/remotes/origin/master",
    "main": "refs/heads/main",
    "master": "refs/heads/master",
    "feat/x": "refs/heads/feat/x",
    "feat/work": "refs/heads/feat/work",
}
_VARIABLE = re.compile(r"\$(\w+)|\$\{(\w+)\}")


@dataclass
class FakeFilesystem:
    """Checkouts at ``/repo`` and a worktree inside it carry the manifest; ``/elsewhere`` not.

    ``manifest`` is the one every guarded checkout carries, ``manifests`` overrides it per root.
    ``merged`` is what every checkout's merge base with its default branch holds, by path
    relative to the checkout; ``None`` stands for a checkout with no such commit.
    """

    manifest: Manifest = MANIFEST
    manifests: dict[Path, Manifest] = field(default_factory=dict)
    home: Path = HOME
    guarded: tuple[Path, ...] = (ROOT, WORKTREE)
    unguarded: tuple[Path, ...] = (UNGUARDED,)
    files: tuple[str, ...] = (
        "/repo/config/types.json",
        "/repo/config/types.node.json",
        "/repo/app/core/money.py",
        "/repo/docs/agents/triage-labels.md",
    )
    contents: dict[str, str] = field(
        default_factory=lambda: {"/repo/docs/agents/triage-labels.md": LABELS_RECORD}
    )
    variables: dict[str, str] = field(
        default_factory=lambda: {"HOME": str(HOME), "CLAUDE_PROJECT_DIR": str(ROOT)}
    )
    merged: dict[str, str] | None = field(default_factory=dict)

    def canonical(self, path: Path) -> Path:
        return Path(posixpath.normpath(path))

    def guarded_checkout(self, path: Path) -> Checkout | None:
        roots = [root for root in (*self.guarded, *self.unguarded) if path.is_relative_to(root)]
        if not roots:
            return None
        nearest = max(roots, key=lambda root: len(root.parts))
        if nearest not in self.guarded:
            return None
        return Checkout(nearest, self.manifests.get(nearest, self.manifest))

    def checkout(self, path: Path) -> Path | None:
        roots = [root for root in (*self.guarded, *self.unguarded) if path.is_relative_to(root)]
        return max(roots, key=lambda root: len(root.parts)) if roots else None

    def read_text(self, path: Path) -> str | None:
        return self.contents.get(str(path))

    def merge_base(self, checkout: Path, relative: str) -> Before | None:
        del checkout
        if self.merged is None:
            return None
        directory = posixpath.dirname(relative)
        beside = (name for name in self.merged if posixpath.dirname(name) == directory)
        return Before(self.merged.get(relative), tuple(map(posixpath.basename, beside)))

    def glob(self, pattern: Path) -> list[Path]:
        return [Path(name) for name in self.files if matches(name, str(pattern))]

    def expand(self, word: str) -> str:
        if word == "~" or word.startswith("~/"):
            word = str(self.home) + word[1:]
        return _VARIABLE.sub(
            lambda m: self.variables.get(m.group(1) or m.group(2), m.group(0)), word
        )


@dataclass
class FakeRepo:
    """Git state: the checked-out branch and whether HEAD is pushed; records the queries."""

    branch: str | None = "feat/work"
    pushed: bool | None = False
    asked: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=lambda: dict(NAMES))

    def current_branch(self, cwd: Path) -> str | None:
        self.asked.append(f"branch {cwd}")
        return self.branch

    def checked_out(self, root: Path) -> str | None:
        self.asked.append(f"head {root}")
        return self.branch

    def default_branches(self, root: Path) -> frozenset[str]:
        del root
        return MAIN_BRANCHES

    def head_is_pushed(self, cwd: Path) -> bool | None:
        self.asked.append(f"pushed {cwd}")
        return self.pushed

    def full_name(self, cwd: Path, name: str) -> str | None:
        self.asked.append(f"name {cwd} {name}")
        return self.names.get(name)


def make_guard(
    repo: FakeRepo | None = None,
    filesystem: FakeFilesystem | None = None,
    manifest: Manifest = MANIFEST,
) -> Guard:
    """A guard over the fakes, the fixture manifest and a plugin installed at ``/plugin``."""
    locator = Locator(
        with_plugin_group(manifest, PLUGIN),
        filesystem or FakeFilesystem(manifest=manifest),
        STANDARD,
    )
    return Guard(locator, repo or FakeRepo())


def rules(command: str, guard: Guard | None = None, cwd: Path = ROOT) -> list[str]:
    """The sorted, distinct rule ids that fire on ``command`` run in ``cwd``."""
    return sorted({verdict.rule for verdict in verdicts(command, guard, cwd)})


def verdicts(command: str, guard: Guard | None = None, cwd: Path = ROOT) -> list[Verdict]:
    """Every verdict on ``command``."""
    return bash.check(command, Context(cwd), guard or make_guard())


def details(command: str, guard: Guard | None = None) -> Sequence[str]:
    """The details of every verdict on ``command``."""
    return [verdict.detail for verdict in verdicts(command, guard)]
