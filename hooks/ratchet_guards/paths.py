"""Where a path lands: the guard group, if any, of a file a tool call would touch.

A path is seen three ways, and the first that hits wins: as an absolute path, against the
plugin group's own patterns; relative to the home directory, against the ``~/`` patterns;
and relative to the nearest enclosing checkout (the closest ancestor holding ``.git``),
against the repository patterns, only if that checkout carries ``guards/manifest.toml``. So
a git worktree nested under ``.claude/worktrees/`` is judged as its own checkout, not as part
of ``.claude/**``. Symlinks and ``..`` are resolved before matching.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ratchet_guards.manifest import Group, Manifest, Scope

_GLOB_CHARS = frozenset("*?[")


class Filesystem(Protocol):
    """The filesystem facts the guard needs."""

    @property
    def home(self) -> Path:
        """The user's home directory."""
        ...

    def canonical(self, path: Path) -> Path:
        """``path`` made absolute with symlinks and ``..`` resolved; it need not exist."""
        ...

    def guarded_root(self, path: Path) -> Path | None:
        """The nearest checkout holding ``path``, if that checkout carries the manifest."""
        ...

    def checkout(self, path: Path) -> Path | None:
        """The nearest checkout holding ``path`` (an ancestor with ``.git``), manifest or not."""
        ...

    def read_text(self, path: Path) -> str | None:
        """The text of the file at ``path``, or ``None`` when there is no readable file."""
        ...

    def glob(self, pattern: Path) -> list[Path]:
        """Existing paths matching a shell glob, hidden files included."""
        ...

    def expand(self, word: str) -> str:
        """``word`` with ``~`` and environment variables expanded."""
        ...


@dataclass(frozen=True, slots=True)
class Hit:
    """A guarded path as the human should read it, and its group."""

    path: str
    group: Group


@dataclass(frozen=True, slots=True)
class Locator:
    """Matches concrete paths against the manifest."""

    manifest: Manifest
    filesystem: Filesystem

    def _views(self, canonical: Path) -> Iterator[tuple[str, Scope]]:
        """``canonical`` written relative to each scope it falls in, strictest scope first."""
        yield canonical.as_posix(), Scope.ABSOLUTE
        home = self.filesystem.home
        if canonical.is_relative_to(home):
            yield canonical.relative_to(home).as_posix(), Scope.HOME
        root = self.filesystem.guarded_root(canonical)
        if root is not None:
            yield canonical.relative_to(root).as_posix(), Scope.REPOSITORY

    def classify(self, path: Path, *, destructive: bool) -> Hit | None:
        """The hit for ``path``; ``destructive`` also catches guard paths below a directory."""
        for relative, scope in self._views(self.filesystem.canonical(path)):
            hit = self._match(relative, destructive=destructive, scope=scope)
            if hit is not None:
                return hit
        return None

    def _match(self, relative: str, *, destructive: bool, scope: Scope) -> Hit | None:
        if destructive:
            group = self.manifest.group_below(relative, scope=scope)
        else:
            group = self.manifest.group_of(relative, scope=scope)
        if group is None:
            return None
        return Hit(f"~/{relative}" if scope is Scope.HOME else relative, group)

    def words(self, words: Iterable[str], cwd: Path, *, destructive: bool) -> list[Hit]:
        """Hits for shell words naming paths relative to ``cwd``; globs are expanded."""
        hits: list[Hit] = []
        for word in words:
            if not word:
                continue
            expanded = self.filesystem.expand(word)
            path = cwd / expanded
            # Why: globbing a literal path finds that path or nothing; the test only saves a stat.
            wild = _GLOB_CHARS & set(expanded)  # pragma: no mutate # -- see the Why above
            found = self.filesystem.glob(path) if wild else []  # pragma: no mutate # -- see the Why
            for candidate in found or [path]:
                hit = self.classify(candidate, destructive=destructive)
                if hit is not None:
                    hits.append(hit)
        return hits
