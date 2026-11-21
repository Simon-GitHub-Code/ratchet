import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path, PurePosixPath

from ratchet_guards.events import FEATURE_STARTS, LOG_NAME, PLUGIN_RECORD
from ratchet_guards.globs import matches, normalised, regex

MANIFEST_PATH = "guards/manifest.toml"
COMMITTED = "Guard paths come from guards/manifest.toml as committed on the default branch."
MANIFEST_GROUP = "manifest-file"
MANIFEST_REASON = (
    "The guards are read from this file as the default branch holds it, so an agent only adds to "
    "it: a path after the others in a group's or door's paths, or whole [[group]] or [[door]] "
    "tables after the ones there, with an Edit or Write on a branch. Any other change, a deletion "
    "or a rename is the owner's, by hand."
)
GONE = (
    f"{MANIFEST_PATH} is gone from the default branch, which held it, so every guard path it named "
    "is locked until the owner puts it back."
)
HOME_PREFIX = "~/"
ABSOLUTE_PREFIX = "/"
PLUGIN_GROUP = "plugin"
PLUGIN_REASON = (
    "The guard's own code, the installed plugins and the user settings that enable them: "
    "changing them can switch the guard off, so the owner changes them by hand."
)
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
    "~/.gemini/config/plugins",
    "~/.gemini/config/plugins/**",
    "~/.gemini/config/hooks.json",
    "~/.gemini/config/config.json",
)


GIT_STATE_GROUP = "git-state"
GIT_STATE_REASON = (
    "The refs, packed refs, HEAD, grafts and object-store pointers of a checkout's git "
    "directory: the path guards and the additive-records check read the default branch through "
    "them, so a write to one can switch the guards off, and git writes them itself (ADR 0016)."
)
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
    REPOSITORY = "repository"
    HOME = "home"
    ABSOLUTE = "absolute"


def scope_of(pattern: str) -> Scope:
    if pattern.startswith(HOME_PREFIX):
        return Scope.HOME
    if pattern.startswith(ABSOLUTE_PREFIX):
        return Scope.ABSOLUTE
    return Scope.REPOSITORY


class ManifestError(ValueError): ...


class EditRule(Enum):
    ASK = "ask"
    DENY = "deny"
    APPEND = "append"


@dataclass(frozen=True, slots=True)
class Group:
    name: str
    reason: str
    patterns: tuple[str, ...]
    edit: EditRule = EditRule.ASK
    excluded: tuple[str, ...] = ()
    source: str = COMMITTED

    @property
    def locked(self) -> bool:
        return self.edit is EditRule.DENY

    @property
    def additive(self) -> bool:
        return self.edit is EditRule.APPEND

    def excludes(self, path: PurePosixPath) -> bool:
        return any(matches(str(path), pattern) for pattern in self.excluded)


class DoorRule(Enum):
    CHANGE = "change"
    REWRITE = "rewrite"
    REMOVAL = "removal"


@dataclass(frozen=True, slots=True)
class Door:
    name: str
    reason: str
    patterns: tuple[str, ...]
    on: DoorRule = DoorRule.CHANGE


@dataclass(frozen=True, slots=True)
class Manifest:
    groups: tuple[Group, ...]
    doors: tuple[Door, ...] = ()

    def _patterns(self, scope: Scope) -> Iterator[tuple[Group, str]]:
        for group in self.groups:
            for pattern in group.patterns:
                if scope_of(pattern) is scope:
                    yield group, pattern.removeprefix(HOME_PREFIX)

    def group_of(self, path: str, *, scope: Scope = Scope.REPOSITORY) -> Group | None:
        candidate = PurePosixPath(path)
        normal = normalised(path)
        for group, pattern in self._patterns(scope):
            if regex(pattern).fullmatch(normal) and not group.excludes(candidate):
                return group
        return None

    def group_below(self, directory: str, *, scope: Scope = Scope.REPOSITORY) -> Group | None:
        prefix = "" if directory == "." else directory.rstrip("/") + "/"
        below = [group for group, pattern in self._patterns(scope) if pattern.startswith(prefix)]
        holding = self.group_of(directory, scope=scope)
        first = below[0] if below else holding
        return next((group for group in (*below, holding) if group and group.locked), first)

    def patterns(self) -> list[str]:
        return [pattern for group in self.groups for pattern in group.patterns]

    def doors_of(self, path: str) -> list[Door]:
        return [
            door for door in self.doors if any(matches(path, pattern) for pattern in door.patterns)
        ]


EMPTY = Manifest(())


GUARD_LOG_GROUP = "guard-log"
GUARD_LOG_REASON = (
    "The guard's log of each tool call it denied or asked about (ADR 0060). Only the guard "
    "writes it, so a reader can trust what it shows."
)
PLUGIN_RECORD_GROUP = "plugin-record"
PLUGIN_RECORD_REASON = (
    "The record of the plugin folder a session runs from. Gate recipes run the scripts it names, "
    "so only the session hook writes it."
)
FEATURE_STARTS_GROUP = "feature-starts"
FEATURE_STARTS_REASON = (
    "The start a one-ticket feature run records and its publish check marks passed. The guard "
    "refuses a commit on the run's branch until the check has passed it, so only the plugin's "
    "scripts write it."
)
GIT_STATE = Manifest(
    (
        Group(GIT_STATE_GROUP, GIT_STATE_REASON, GIT_STATE_PATTERNS, EditRule.DENY, source=""),
        Group(GUARD_LOG_GROUP, GUARD_LOG_REASON, (f".git/{LOG_NAME}",), EditRule.DENY, source=""),
        Group(
            PLUGIN_RECORD_GROUP,
            PLUGIN_RECORD_REASON,
            (f".git/{PLUGIN_RECORD}",),
            EditRule.DENY,
            source="",
        ),
        Group(
            FEATURE_STARTS_GROUP,
            FEATURE_STARTS_REASON,
            (f".git/{FEATURE_STARTS}", f".git/{FEATURE_STARTS}/**"),
            EditRule.DENY,
            source="",
        ),
    )
)
MANIFEST_LOCK = Group(MANIFEST_GROUP, MANIFEST_REASON, (MANIFEST_PATH,), EditRule.DENY, source="")


def plugin_group(plugin_root: Path) -> Group:
    root = plugin_root.as_posix()
    patterns = (root, f"{root}/**", *PLUGIN_HOME_PATTERNS)
    return Group(PLUGIN_GROUP, PLUGIN_REASON, patterns, EditRule.DENY, source="")


def with_manifest_lock(manifest: Manifest) -> Manifest:
    own = manifest.group_of(MANIFEST_PATH)
    if own is not None and own.locked:
        return manifest
    return Manifest((MANIFEST_LOCK, *manifest.groups), manifest.doors)


def gone(manifest: Manifest) -> Manifest:
    groups = (replace(group, edit=EditRule.DENY, source=GONE) for group in manifest.groups)
    return Manifest(tuple(groups), manifest.doors)


def grown(before: Manifest, text: str) -> str | None:
    try:
        after = parse(text)
    except ManifestError as error:
        return f"the manifest it leaves does not parse ({error})"
    if not _extended(before.groups, after.groups):
        return "it changes, removes or reorders a [[group]] the default branch holds"
    if not _extended(before.doors, after.doors):
        return "it changes, removes or reorders a [[door]] the default branch holds"
    for index, (old, new) in enumerate(zip(before.groups, after.groups, strict=False)):
        for pattern in new.patterns[len(old.patterns) :]:
            below = (group for group in after.groups[index + 1 :] if _overlaps(pattern, group))
            taken = next(below, None)
            if taken is not None:
                return (
                    f"its pattern {pattern!r} in [[group]] {new.name!r} also matches paths of "
                    f"{taken.name!r}, a group below it"
                )
    return None


def _extended(before: Sequence[Group | Door], after: Sequence[Group | Door]) -> bool:
    kept = after[: len(before)]
    return len(kept) == len(before) and all(
        replace(new, patterns=new.patterns[: len(old.patterns)]) == old
        for old, new in zip(before, kept, strict=True)
    )


def _overlaps(pattern: str, group: Group) -> bool:
    return any(matches(other, pattern) or matches(pattern, other) for other in group.patterns)


def with_plugin_group(manifest: Manifest, plugin_root: Path) -> Manifest:
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
