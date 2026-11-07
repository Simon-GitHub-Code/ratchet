"""Fakes and fixtures shared by the guard tests: a fake filesystem and git, a fixture manifest."""

import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ratchet_guards import bash
from ratchet_guards.context import Context, Guard
from ratchet_guards.manifest import Manifest, parse, with_plugin_group
from ratchet_guards.paths import Locator
from ratchet_guards.verdict import Verdict

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_TEXT = (Path(__file__).resolve().parent / "fixtures/manifest.toml").read_text(
    encoding="utf-8"
)
MANIFEST = parse(MANIFEST_TEXT)
PLUGIN = Path("/plugin")
ROOT = Path("/repo")
WORKTREE = ROOT / ".claude/worktrees/agent-1"
UNGUARDED = Path("/elsewhere")
HOME = Path("/home/agent")
_VARIABLE = re.compile(r"\$(\w+)|\$\{(\w+)\}")


@dataclass
class FakeFilesystem:
    """Checkouts at ``/repo`` and a worktree inside it carry the manifest; ``/elsewhere`` not."""

    home: Path = HOME
    guarded: tuple[Path, ...] = (ROOT, WORKTREE)
    unguarded: tuple[Path, ...] = (UNGUARDED,)
    files: tuple[str, ...] = (
        "/repo/config/types.json",
        "/repo/config/types.node.json",
        "/repo/app/core/money.py",
    )
    variables: dict[str, str] = field(
        default_factory=lambda: {"HOME": str(HOME), "CLAUDE_PROJECT_DIR": str(ROOT)}
    )

    def canonical(self, path: Path) -> Path:
        return Path(posixpath.normpath(path))

    def guarded_root(self, path: Path) -> Path | None:
        roots = [root for root in (*self.guarded, *self.unguarded) if path.is_relative_to(root)]
        if not roots:
            return None
        nearest = max(roots, key=lambda root: len(root.parts))
        return nearest if nearest in self.guarded else None

    def glob(self, pattern: Path) -> list[Path]:
        return [Path(name) for name in self.files if PurePosixPath(name).full_match(str(pattern))]

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

    def current_branch(self, cwd: Path) -> str | None:
        self.asked.append(f"branch {cwd}")
        return self.branch

    def head_is_pushed(self, cwd: Path) -> bool | None:
        self.asked.append(f"pushed {cwd}")
        return self.pushed


def make_guard(
    repo: FakeRepo | None = None,
    filesystem: FakeFilesystem | None = None,
    manifest: Manifest = MANIFEST,
) -> Guard:
    """A guard over the fakes, the fixture manifest and a plugin installed at ``/plugin``."""
    locator = Locator(with_plugin_group(manifest, PLUGIN), filesystem or FakeFilesystem())
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
