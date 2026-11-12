"""The guard manifest, ``guards/manifest.toml`` under the repository root, plus the plugin's own.

A pattern is matched against a POSIX path with the ``PurePosixPath.full_match`` rules
(``ratchet_guards.globs``, since the system ``python3`` may predate 3.13): ``*`` and ``?``
stay within one segment and ``**`` spans any number of segments. Its ``Scope`` is read from
its first characters: ``~/`` is matched relative to the user's home directory, ``/`` against
the absolute path (only the implicit plugin group uses these; the file may not), and
anything else relative to the repository. A group's optional ``edit`` is ``"ask"`` (the
default), ``"deny"``, for paths Claude Code's bypass mode would otherwise let through, or
``"append"``, for records an agent may add to and only the owner changes: an edit that
``ratchet_guards.records`` finds additive passes, and every other one asks (ADR 0014). Its
optional ``exclude`` lists patterns its ``paths`` do not cover, such as agent worktrees under
``.claude/worktrees/``, which are checkouts of their own. A group whose ``paths`` list is
empty matches nothing: the adopt template ships such groups behind ``# bind:`` comments until
adopt fills them, and the conformance gate reports any still empty.

The file also lists the repository's one-way doors, as ``[[door]]`` tables: paths whose change
cannot be taken back once it ships (ADR 0021). A door is no guard group: the hook, ``guard-diff``
and the permission rules never read it, so an agent builds a change that goes through one without
a prompt. The merge judge (``scripts/merge_ready.py``) reads it, and sends such a change to the
owner. A door's ``on`` says what shuts it: ``"change"`` (the default) is any change to a matching
path, ``"rewrite"`` a change to or deletion of a file the default branch holds, and ``"removal"``
a deletion, or a change that does more than add to the file.

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

from ratchet_guards.globs import matches

MANIFEST_PATH = "guards/manifest.toml"
HOME_PREFIX = "~/"
ABSOLUTE_PREFIX = "/"
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


GIT_STATE_GROUP = "git-state"
GIT_STATE_REASON = (
    "The refs, packed refs, HEAD, grafts and object-store pointers of a checkout's git "
    "directory: the path guards and the additive-records check read the default branch through "
    "them, so a write to one can switch the guards off, and git writes them itself (ADR 0016)."
)
# Why: the guard reads the default branch through these files, so the hook protects them in every
# checkout, adopted or not. The template manifest's `local-git` group lists them too, so that
# `just settings` writes an `Edit` rule for each; a test keeps the two in step.
GIT_STATE_PATTERNS = (
    ".git/refs",
    ".git/refs/**",
    ".git/packed-refs",
    ".git/HEAD",
    ".git/info/grafts",
    ".git/shallow",
    ".git/objects/info",
    ".git/objects/info/**",
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


class EditRule(Enum):
    """What the hook answers to an edit of a group's paths."""

    ASK = "ask"
    DENY = "deny"
    APPEND = "append"


@dataclass(frozen=True, slots=True)
class Group:
    """Guard paths that share a reason, and the answer an edit of one gets."""

    name: str
    reason: str
    patterns: tuple[str, ...]
    edit: EditRule = EditRule.ASK
    excluded: tuple[str, ...] = ()

    @property
    def locked(self) -> bool:
        """Whether an edit is denied outright (``edit = "deny"``)."""
        return self.edit is EditRule.DENY

    @property
    def additive(self) -> bool:
        """Whether an edit that only adds passes without asking (``edit = "append"``)."""
        return self.edit is EditRule.APPEND

    def excludes(self, path: PurePosixPath) -> bool:
        """Whether one of the group's ``exclude`` patterns matches ``path``."""
        return any(matches(str(path), pattern) for pattern in self.excluded)


class DoorRule(Enum):
    """What makes a change to a door's paths one the owner merges."""

    CHANGE = "change"
    REWRITE = "rewrite"
    REMOVAL = "removal"


@dataclass(frozen=True, slots=True)
class Door:
    """Paths whose change cannot be taken back once it ships, and what shuts the door."""

    name: str
    reason: str
    patterns: tuple[str, ...]
    on: DoorRule = DoorRule.CHANGE


@dataclass(frozen=True, slots=True)
class Manifest:
    """Every guard group, in file order, and the one-way doors beside them."""

    groups: tuple[Group, ...]
    doors: tuple[Door, ...] = ()

    def _patterns(self, scope: Scope) -> Iterator[tuple[Group, str]]:
        for group in self.groups:
            for pattern in group.patterns:
                if scope_of(pattern) is scope:
                    yield group, pattern.removeprefix(HOME_PREFIX)

    def group_of(self, path: str, *, scope: Scope = Scope.REPOSITORY) -> Group | None:
        """The group guarding ``path``, written relative to ``scope``."""
        candidate = PurePosixPath(path)
        for group, pattern in self._patterns(scope):
            if matches(path, pattern) and not group.excludes(candidate):
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

    def doors_of(self, path: str) -> list[Door]:
        """Every one-way door ``path``, written relative to the repository, goes through."""
        return [
            door for door in self.doors if any(matches(path, pattern) for pattern in door.patterns)
        ]


EMPTY = Manifest(())


GIT_STATE = Manifest((Group(GIT_STATE_GROUP, GIT_STATE_REASON, GIT_STATE_PATTERNS, EditRule.DENY),))


def plugin_group(plugin_root: Path) -> Group:
    """The locked group over the plugin at ``plugin_root`` (absolute) and the user's plugins."""
    root = plugin_root.as_posix()
    return Group(
        PLUGIN_GROUP, PLUGIN_REASON, (root, f"{root}/**", *PLUGIN_HOME_PATTERNS), EditRule.DENY
    )


def with_plugin_group(manifest: Manifest, plugin_root: Path) -> Manifest:
    """``manifest`` with the plugin group prepended, so it wins every match it shares."""
    return Manifest((plugin_group(plugin_root), *manifest.groups), manifest.doors)


def _text(table: dict[str, object], key: str, kind: str = "group") -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"{kind} {key!r} must be a non-empty string"
        raise ManifestError(msg)
    return value


def _pattern(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value:
        msg = f"pattern {value!r} must be a non-empty relative glob without '..'"
        raise ManifestError(msg)
    return value


def _edit_rule(value: object) -> EditRule:
    rules = {rule.value: rule for rule in EditRule}
    if not isinstance(value, str) or value not in rules:
        msg = f"group 'edit' must be one of {sorted(rules)}, not {value!r}"
        raise ManifestError(msg)
    return rules[value]


def _group(table: object) -> Group:
    if not isinstance(table, dict):
        msg = "every [[group]] must be a table"
        raise ManifestError(msg)
    paths = table.get("paths")
    if not isinstance(paths, list):
        msg = "group 'paths' must be a list"
        raise ManifestError(msg)
    excluded = table.get("exclude", [])
    if not isinstance(excluded, list):
        msg = "group 'exclude' must be a list"
        raise ManifestError(msg)
    patterns = tuple(map(_pattern, paths))
    name, reason = _text(table, "name"), _text(table, "reason")
    excludes = tuple(map(_pattern, excluded))
    return Group(name, reason, patterns, _edit_rule(table.get("edit", "ask")), excludes)


def _door_rule(value: object) -> DoorRule:
    rules = {rule.value: rule for rule in DoorRule}
    if not isinstance(value, str) or value not in rules:
        msg = f"door 'on' must be one of {sorted(rules)}, not {value!r}"
        raise ManifestError(msg)
    return rules[value]


def _door(table: object) -> Door:
    if not isinstance(table, dict):
        msg = "every [[door]] must be a table"
        raise ManifestError(msg)
    paths = table.get("paths")
    if not isinstance(paths, list):
        msg = "door 'paths' must be a list"
        raise ManifestError(msg)
    patterns = tuple(map(_pattern, paths))
    name, reason = _text(table, "name", "door"), _text(table, "reason", "door")
    return Door(name, reason, patterns, _door_rule(table.get("on", "change")))


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
    doors = document.get("door", [])
    if not isinstance(doors, list):
        msg = "'door' must be an array of tables; write [[door]]"
        raise ManifestError(msg)
    return Manifest(tuple(map(_group, tables)), tuple(map(_door, doors)))
