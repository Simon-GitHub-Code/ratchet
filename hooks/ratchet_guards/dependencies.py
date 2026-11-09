"""Which guard-path changes only change dependencies, and what they change.

A dependency manifest (``pyproject.toml``, ``Cargo.toml``, ``package.json``) holds gate
configuration beside the dependency list, so the whole file is a guard path. Adding a
dependency is a decision the pull request states, not a gate change; reporting it as one
stops every owner-away feature run that adds a library, and teaches the owner to skim the
guard report. A change is dependency-only when both sides parse and are equal once the
dependency tables and the package version are set aside. A lockfile holds nothing else. Any
other change, a parse failure, a file created or deleted, an entry pointed at a direct source
(git, a path, a URL) or a locked version that falls stays a guard change, since each is a way
to swap or roll back a gate tool.

The version changes are listed by package name, so a gate tool downgraded through its
lockfile is in front of the owner at the merge.
"""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import PurePosixPath

LOCKFILES = frozenset(
    {"uv.lock", "Cargo.lock", "poetry.lock", "pnpm-lock.yaml", "package-lock.json"}
    | {"yarn.lock", "bun.lock", "Gemfile.lock", "go.sum", "composer.lock"}
)
_TOML_LOCKS = frozenset({"uv.lock", "Cargo.lock", "poetry.lock"})
_CARGO_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
_NPM_TABLES = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
SNAPSHOT_SEPARATOR = "__"
# Why: a dependency fetched from a fork or a local path can replace a gate tool, so a change
# that points an entry at one is a guard change, as ADR 0011's refutation criterion asks.
_DIRECT_SOURCE = re.compile(r'git\+|github:|gitlab:|file:|link:|https?://|"(git|path)"|\s@\s')
_NUMBERS = re.compile(r"\d+")

Data = dict[str, object]
Deps = dict[str, str]


def _table(data: Data, key: str) -> Data:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _requirements(items: object, prefix: str) -> Deps:
    """PEP 508 strings by package name."""
    found: Deps = {}
    for item in items if isinstance(items, list) else []:
        match = _REQUIREMENT_NAME.match(str(item))
        if match:
            found[f"{prefix}{match.group(1).lower()}"] = str(item)
    return found


def _pyproject(data: Data) -> tuple[Data, Deps]:
    project = dict(_table(data, "project"))
    deps = _requirements(project.pop("dependencies", None), "")
    for extra, items in _table(project, "optional-dependencies").items():
        deps |= _requirements(items, f"{extra}:")
    project.pop("optional-dependencies", None)
    project.pop("version", None)
    for group, items in _table(data, "dependency-groups").items():
        deps |= _requirements(items, f"{group}:")
    rest = {k: v for k, v in data.items() if k not in {"project", "dependency-groups"}}
    return rest | {"project": project}, deps


def _named(table: Data, prefix: str) -> Deps:
    return {f"{prefix}{name}": json.dumps(spec, sort_keys=True) for name, spec in table.items()}


def _cargo(data: Data) -> tuple[Data, Deps]:
    rest = dict(data)
    deps: Deps = {}
    for key in _CARGO_TABLES:
        deps |= _named(_table(data, key), f"{key}:")
        rest.pop(key, None)
    targets = {}
    for target, table in _table(data, "target").items():
        kept = dict(table) if isinstance(table, dict) else {}
        for key in _CARGO_TABLES:
            deps |= _named(_table(kept, key), f"{target}:{key}:")
            kept.pop(key, None)
        targets[target] = kept
    rest["target"] = targets
    workspace = dict(_table(data, "workspace"))
    deps |= _named(_table(workspace, "dependencies"), "workspace:")
    workspace.pop("dependencies", None)
    rest["workspace"] = workspace
    package = dict(_table(data, "package"))
    package.pop("version", None)
    rest["package"] = package
    return rest, deps


def _package_json(data: Data) -> tuple[Data, Deps]:
    rest = {k: v for k, v in data.items() if k not in {*_NPM_TABLES, "version"}}
    deps: Deps = {}
    for key in _NPM_TABLES:
        deps |= {f"{key}:{n}": str(v) for n, v in _table(data, key).items()}
    return rest, deps


def _locked(data: Data) -> tuple[Data, Deps]:
    packages = data.get("package")
    deps: Deps = {}
    for item in packages if isinstance(packages, list) else []:
        if isinstance(item, dict) and "name" in item:
            deps[str(item["name"])] = str(item.get("version", ""))
    return {}, deps


Splitter = Callable[[Data], tuple[Data, Deps]]
_MANIFESTS: dict[str, tuple[Callable[[str], object], Splitter]] = {
    "pyproject.toml": (tomllib.loads, _pyproject),
    "Cargo.toml": (tomllib.loads, _cargo),
    "package.json": (json.loads, _package_json),
    **dict.fromkeys(_TOML_LOCKS, (tomllib.loads, _locked)),
}


def file_name(path: str) -> str:
    """The name a path's file has in the tree; a snapshot is named after the file it copies."""
    return PurePosixPath(path).name.rsplit(SNAPSHOT_SEPARATOR, 1)[-1]


def recognised(path: str) -> bool:
    """Whether ``path`` is a dependency manifest or lockfile, or a snapshot of one."""
    name = file_name(path)
    return name in _MANIFESTS or name in LOCKFILES


def _split(name: str, text: str) -> tuple[Data, Deps] | None:
    reader = _MANIFESTS.get(name)
    if reader is None:
        return ({}, {}) if name in LOCKFILES else None
    load, split = reader
    try:
        data = load(text)
    except ValueError:
        return None
    return split(data) if isinstance(data, dict) else None


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _NUMBERS.findall(text))


def _additive(name: str, old: Deps, new: Deps) -> bool:
    """No entry points at a direct source, and no locked version falls or becomes unreadable."""
    for key, spec in new.items():
        if old.get(key) == spec:
            continue
        if _DIRECT_SOURCE.search(spec):
            return False
        if name in _TOML_LOCKS and key in old:
            before, after = _version(old[key]), _version(spec)
            if not before or not after or after < before:
                return False
    return True


def changes(path: str, before: str | None, after: str | None) -> list[str] | None:
    """The dependency changes when ``path`` changed only its dependencies, else ``None``.

    Each change is ``name: old -> new``, with ``(none)`` for an added or removed entry; a
    lockfile whose format is not read reports only that it changed.
    """
    if before is None or after is None:
        return None
    name = file_name(path)
    old, new = _split(name, before), _split(name, after)
    if old is None or new is None or old[0] != new[0]:
        return None
    if name in LOCKFILES and name not in _MANIFESTS:
        return [f"{name}: changed"]
    if not _additive(name, old[1], new[1]):
        return None
    keys = sorted(old[1].keys() | new[1].keys())
    return [
        f"{key}: {old[1].get(key, '(none)')} -> {new[1].get(key, '(none)')}"
        for key in keys
        if old[1].get(key) != new[1].get(key)
    ]
