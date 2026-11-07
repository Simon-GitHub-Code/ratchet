"""The guard manifest, ``guards/manifest.toml`` under the repository root, plus the plugin's own.

A pattern is matched against a POSIX path with ``PurePosixPath.full_match``: ``*`` and ``?``
stay within one segment and ``**`` spans any number of segments. Its ``Scope`` is read from
its first characters: ``~/`` is matched relative to the user's home directory, ``/`` against
the absolute path (only the implicit plugin group uses these; the file may not), and
anything else relative to the repository. A group's optional ``edit`` is ``"ask"`` (the
default) or ``"deny"``, for paths Claude Code's bypass mode would otherwise let through. Its
optional ``exclude`` lists patterns its ``paths`` do not cover, such as agent worktrees under
``.claude/worktrees/``, which are checkouts of their own.

The manifest is optional. When the file is absent the hook judges with ``EMPTY``, so no
repository path is a guard path and only the command rules apply (gate bypasses, force
pushes, pushes to main, history rewrites, GitHub protection, label and settings changes,
nested Claude Code sessions and plugin removal). Whether or not a manifest exists,
``with_plugin_group`` prepends the locked ``plugin`` group: the plugin's own directory, the
installed plugins under ``~/.claude/plugins/`` and the user settings that enable them.
"""

import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

MANIFEST_PATH = "guards/manifest.toml"
HOME_PREFIX = "~/"
ABSOLUTE_PREFIX = "/"
EDIT_DECISIONS = frozenset({"ask", "deny"})
PLUGIN_GROUP = "plugin"
PLUGIN_REASON = (
    "The guard's own code, the installed plugins and the user settings that enable them: "
    "changing them can switch the guard off, so the owner changes them by hand."
)
# Why: the guard runs in five harnesses; each one's installed plugins and the settings that
# enable them are the switch an agent must not reach.
PLUGIN_HOME_PATTERNS = (
    "~/.claude/plugins",
    "~/.claude/plugins/**",
    "~/.claude/settings.json",
    "~/.claude/settings.local.json",
    "~/.grok/installed-plugins",
    "~/.grok/installed-plugins/**",
    "~/.grok/hooks",
    "~/.grok/hooks/**",
    "~/.grok/config.toml",
    "~/.codex/plugins",
    "~/.codex/plugins/**",
    "~/.codex/hooks.json",
    "~/.codex/config.toml",
    "~/.pi/agent/extensions",
    "~/.pi/agent/extensions/**",
    "~/.pi/agent/settings.json",
    "~/.config/opencode/plugins",
    "~/.config/opencode/plugins/**",
    "~/.config/opencode/opencode.json",
)


class Scope(Enum):
    """What a pattern's path is relative to."""

    REPOSITORY = "repository"
    HOME = "home"
    ABSOLUTE = "absolute"


def scope_of(pattern: str) -> Scope:
    """The scope a pattern's prefix declares."""
    if pattern.startswith(HOME_PREFIX):
        return Scope.HOME
    if pattern.startswith(ABSOLUTE_PREFIX):
        return Scope.ABSOLUTE
    return Scope.REPOSITORY


class ManifestError(ValueError):
    """The manifest does not have the documented shape."""


@dataclass(frozen=True, slots=True)
class Group:
    """Guard paths that share a reason; ``locked`` (``edit = "deny"``) denies edits outright."""

    name: str
    reason: str
    patterns: tuple[str, ...]
    locked: bool = False
    excluded: tuple[str, ...] = ()

    def excludes(self, path: PurePosixPath) -> bool:
        """Whether one of the group's ``exclude`` patterns matches ``path``."""
        return any(path.full_match(pattern) for pattern in self.excluded)


@dataclass(frozen=True, slots=True)
class Manifest:
    """Every guard group, in file order."""

    groups: tuple[Group, ...]

    def _patterns(self, scope: Scope) -> Iterator[tuple[Group, str]]:
        for group in self.groups:
            for pattern in group.patterns:
                if scope_of(pattern) is scope:
                    yield group, pattern.removeprefix(HOME_PREFIX)

    def group_of(self, path: str, *, scope: Scope = Scope.REPOSITORY) -> Group | None:
        """The group guarding ``path``, written relative to ``scope``."""
        candidate = PurePosixPath(path)
        for group, pattern in self._patterns(scope):
            if candidate.full_match(pattern) and not group.excludes(candidate):
                return group
        return None

    def group_below(self, directory: str, *, scope: Scope = Scope.REPOSITORY) -> Group | None:
        """The group guarding something at or below ``directory`` (``"."`` is the top)."""
        prefix = "" if directory == "." else directory.rstrip("/") + "/"
        for group, pattern in self._patterns(scope):
            if pattern.startswith(prefix):
                return group
        return self.group_of(directory, scope=scope)

    def patterns(self) -> list[str]:
        """Every pattern as written, in file order."""
        return [pattern for group in self.groups for pattern in group.patterns]


EMPTY = Manifest(())


def plugin_group(plugin_root: Path) -> Group:
    """The locked group over the plugin at ``plugin_root`` (absolute) and the user's plugins."""
    root = plugin_root.as_posix()
    return Group(
        PLUGIN_GROUP, PLUGIN_REASON, (root, f"{root}/**", *PLUGIN_HOME_PATTERNS), locked=True
    )


def with_plugin_group(manifest: Manifest, plugin_root: Path) -> Manifest:
    """``manifest`` with the plugin group prepended, so it wins every match it shares."""
    return Manifest((plugin_group(plugin_root), *manifest.groups))


def _text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"group {key!r} must be a non-empty string"
        raise ManifestError(msg)
    return value


def _pattern(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value:
        msg = f"pattern {value!r} must be a non-empty relative glob without '..'"
        raise ManifestError(msg)
    return value


def _group(table: object) -> Group:
    if not isinstance(table, dict):
        msg = "every [[group]] must be a table"
        raise ManifestError(msg)
    paths = table.get("paths")
    if not isinstance(paths, list) or not paths:
        msg = "group 'paths' must be a non-empty list"
        raise ManifestError(msg)
    excluded = table.get("exclude", [])
    if not isinstance(excluded, list):
        msg = "group 'exclude' must be a list"
        raise ManifestError(msg)
    edit = table.get("edit", "ask")
    if edit not in EDIT_DECISIONS:
        msg = f"group 'edit' must be one of {sorted(EDIT_DECISIONS)}, not {edit!r}"
        raise ManifestError(msg)
    patterns = tuple(map(_pattern, paths))
    name, reason = _text(table, "name"), _text(table, "reason")
    excludes = tuple(map(_pattern, excluded))
    return Group(name, reason, patterns, locked=edit == "deny", excluded=excludes)


def parse(text: str) -> Manifest:
    """Parse manifest TOML; raise ``ManifestError`` on any departure from the documented shape."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(str(error)) from error
    tables = document.get("group")
    if not isinstance(tables, list) or not tables:
        msg = "the manifest needs at least one [[group]]"
        raise ManifestError(msg)
    return Manifest(tuple(map(_group, tables)))
