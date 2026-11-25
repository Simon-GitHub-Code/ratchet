import posixpath
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from ratchet_guards.early import Kind, kind_of
from ratchet_guards.manifest import (
    EMPTY,
    GIT_STATE,
    Group,
    Manifest,
    Scope,
    with_manifest_lock,
)
from ratchet_guards.records import Before

_GLOB_CHARS = frozenset("*?[")


class Filesystem(Protocol):
    @property
    def home(self) -> Path: ...

    def canonical(self, path: Path) -> Path: ...

    def guarded_checkout(self, path: Path) -> "Checkout | None": ...

    def checkout(self, path: Path) -> Path | None: ...

    def read_text(self, path: Path) -> str | None: ...

    def merge_base(self, checkout: Path, relative: str) -> Before | None: ...

    def glob(self, pattern: Path) -> list[Path]: ...

    def expand(self, word: str) -> str: ...


@dataclass(frozen=True, slots=True)
class Checkout:
    root: Path
    manifest: Manifest


@dataclass(frozen=True, slots=True)
class Hit:
    path: str
    group: Group
    at: Path


@dataclass(frozen=True, slots=True)
class Acting:
    path: str
    kind: Kind


@dataclass(frozen=True, slots=True)
class Locator:
    manifest: Manifest
    filesystem: Filesystem
    standard: Manifest = EMPTY

    def _views(self, canonical: Path) -> Iterator[tuple[str, Scope, Manifest]]:
        yield canonical.as_posix(), Scope.ABSOLUTE, self.manifest
        home = self.filesystem.home
        if canonical.is_relative_to(home):
            yield canonical.relative_to(home).as_posix(), Scope.HOME, self.manifest
        checkout = self.filesystem.guarded_checkout(canonical)
        if checkout is not None:
            relative = canonical.relative_to(checkout.root).as_posix()
            yield relative, Scope.REPOSITORY, with_manifest_lock(checkout.manifest)
        root = self.filesystem.checkout(canonical)
        if root is not None and canonical != root:
            yield canonical.relative_to(root).as_posix(), Scope.REPOSITORY, GIT_STATE

    def classify(self, path: Path, *, destructive: bool) -> Hit | None:
        canonical = self.filesystem.canonical(path)
        for relative, scope, manifest in self._views(canonical):
            group = self._match(manifest, relative, destructive=destructive, scope=scope)
            if group is not None:
                shown = f"~/{relative}" if scope is Scope.HOME else relative
                return Hit(shown, group, canonical)
        return None

    @staticmethod
    def _match(
        manifest: Manifest, relative: str, *, destructive: bool, scope: Scope
    ) -> Group | None:
        if destructive:
            return manifest.group_below(relative, scope=scope)
        return manifest.group_of(relative, scope=scope)

    def relative(self, words: Iterable[str]) -> list[str]:
        expanded = ((word, self.filesystem.expand(word)) for word in words)
        return [word for word, path in expanded if path and not path.startswith(("/", "~"))]

    def could_hit(self, words: Iterable[str], *, destructive: bool) -> list[str]:
        found: list[str] = []
        for word in self.relative(words):
            expanded = self.filesystem.expand(word)
            relative = PurePosixPath(posixpath.normpath(expanded))
            relative = PurePosixPath(*[part for part in relative.parts if part != ".."])
            if any(
                self._match(
                    manifest, relative.as_posix(), destructive=destructive, scope=Scope.REPOSITORY
                )
                for manifest in (self.manifest, self.standard, GIT_STATE)
            ):
                found.append(word)
        return found

    def paths(self, words: Iterable[str], cwd: Path) -> list[Path]:
        paths: list[Path] = []
        for word in words:
            if not word:
                continue
            expanded = self.filesystem.expand(word)
            path = cwd / expanded
            wild = _GLOB_CHARS & set(expanded)  # pragma: no mutate # -- a literal globs to itself
            found = self.filesystem.glob(path) if wild else []  # pragma: no mutate # -- saves stats
            paths += found or [path]
        return paths

    @staticmethod
    def literal(names: Iterable[str]) -> list[str]:
        return [name for name in names if name and not _GLOB_CHARS & set(name)]

    def named(self, names: Iterable[str], cwd: Path) -> list[Path]:
        return [cwd / self.filesystem.expand(name) for name in self.literal(names)]

    def hits(self, paths: Iterable[Path], *, destructive: bool) -> list[Hit]:
        found = (self.classify(path, destructive=destructive) for path in paths)
        return [hit for hit in found if hit is not None]

    def words(self, words: Iterable[str], cwd: Path, *, destructive: bool) -> list[Hit]:
        return self.hits(self.paths(words, cwd), destructive=destructive)

    def acting(self, path: Path) -> Acting | None:
        canonical = self.filesystem.canonical(path)
        checkout = self.filesystem.guarded_checkout(canonical)
        if checkout is None:
            return None
        relative = canonical.relative_to(checkout.root).as_posix()
        kind = kind_of(relative)
        return None if kind is None else Acting(relative, kind)
