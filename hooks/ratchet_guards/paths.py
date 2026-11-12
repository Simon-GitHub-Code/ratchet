"""Where a path lands: the guard group, if any, of a file a tool call would touch.

A path is seen three ways, and the first that hits wins: as an absolute path, against the
plugin group's own patterns; relative to the home directory, against the ``~/`` patterns;
and relative to the nearest enclosing checkout (the closest ancestor holding ``.git``),
against that checkout's own repository patterns, only if it has a manifest committed on its
default branch. The checkout is the path's, never the session's: an agent rooted in another
directory is judged by the manifest of the checkout it edits. So a git worktree nested under
``.claude/worktrees/`` is judged as its own checkout, not as part of ``.claude/**``. Symlinks
and ``..`` are resolved before matching.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ratchet_guards.manifest import GIT_STATE, Group, Manifest, Scope
from ratchet_guards.records import Before

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

    def guarded_checkout(self, path: Path) -> "Checkout | None":
        """The nearest checkout holding ``path`` with the manifest committed on its default branch.

        ``None`` when ``path`` is in no checkout, or its checkout has no committed manifest.
        """
        ...

    def checkout(self, path: Path) -> Path | None:
        """The nearest checkout holding ``path`` (an ancestor with ``.git``), manifest or not."""
        ...

    def read_text(self, path: Path) -> str | None:
        """The text of the file at ``path``, or ``None`` when there is no readable file."""
        ...

    def merge_base(self, checkout: Path, relative: str) -> Before | None:
        """The file at ``relative`` as the merge base of ``HEAD`` and the default branch has it.

        ``None`` when ``checkout`` has no such commit to read: no default branch, no ``HEAD``.
        """
        ...

    def glob(self, pattern: Path) -> list[Path]:
        """Existing paths matching a shell glob, hidden files included."""
        ...

    def expand(self, word: str) -> str:
        """``word`` with ``~`` and environment variables expanded."""
        ...


@dataclass(frozen=True, slots=True)
class Checkout:
    """A checkout and the manifest committed on its default branch."""

    root: Path
    manifest: Manifest


@dataclass(frozen=True, slots=True)
class Hit:
    """A guarded path as the human should read it, and its group."""

    path: str
    group: Group


@dataclass(frozen=True, slots=True)
class Locator:
    """Matches concrete paths against the manifests.

    ``manifest`` (the plugin group, and the session's checkout manifest) answers the absolute
    and home views. The repository view is answered by the manifest of the checkout holding
    the path, which ``filesystem`` supplies.
    """

    manifest: Manifest
    filesystem: Filesystem

    def _views(self, canonical: Path) -> Iterator[tuple[str, Scope, Manifest]]:
        """``canonical`` written relative to each scope it falls in, with the manifest for it.

        Strictest scope first. The last view is the checkout's git directory, which no manifest
        has to name (``GIT_STATE``); the checkout itself is not in it, so that removing a whole
        scratch repository is not a write to its refs.
        """
        yield canonical.as_posix(), Scope.ABSOLUTE, self.manifest
        home = self.filesystem.home
        if canonical.is_relative_to(home):
            yield canonical.relative_to(home).as_posix(), Scope.HOME, self.manifest
        checkout = self.filesystem.guarded_checkout(canonical)
        if checkout is not None:
            relative = canonical.relative_to(checkout.root).as_posix()
            yield relative, Scope.REPOSITORY, checkout.manifest
        root = self.filesystem.checkout(canonical)
        if root is not None and canonical != root:
            yield canonical.relative_to(root).as_posix(), Scope.REPOSITORY, GIT_STATE

    def classify(self, path: Path, *, destructive: bool) -> Hit | None:
        """The hit for ``path``; ``destructive`` also catches guard paths below a directory."""
        for relative, scope, manifest in self._views(self.filesystem.canonical(path)):
            hit = self._match(manifest, relative, destructive=destructive, scope=scope)
            if hit is not None:
                return hit
        return None

    @staticmethod
    def _match(manifest: Manifest, relative: str, *, destructive: bool, scope: Scope) -> Hit | None:
        if destructive:
            group = manifest.group_below(relative, scope=scope)
        else:
            group = manifest.group_of(relative, scope=scope)
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
