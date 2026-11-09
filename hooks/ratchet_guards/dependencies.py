"""What a guard-path change does to dependencies, when that is all it does.

A dependency manifest (``pyproject.toml``, ``Cargo.toml``, ``package.json``) holds gate
configuration beside the dependency list, so the whole file is a guard path, and so is its
lockfile. ``guard-diff`` lists every changed guard path; for a manifest, a lockfile or a
snapshot of one it adds what the change does to dependencies when that is all it does, so the
owner reads the versions, and the pipeline can match the added names against the ones its spec
named (ADR 0011).

A manifest change is dependency-only when both sides parse, they are equal once the dependency
tables and the package's own version are set aside, and every entry that changed is a new one
fetched from the registry by name and version range. A lockfile change is dependency-only when
no artifact changed in place at the same version, every new artifact comes from a registry the
lockfile already uses (or the ecosystem's default), and no package's highest version falls. Any
other change returns the reason it is not: each is a way to swap a gate tool or roll it back.
"""

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

LOCKFILES = frozenset(
    {"uv.lock", "Cargo.lock", "poetry.lock", "pnpm-lock.yaml", "package-lock.json"}
    | {"yarn.lock", "bun.lock", "Gemfile.lock", "go.sum", "composer.lock"}
)
SNAPSHOT_SEPARATOR = "__"
_CARGO_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
_CARGO_KEYS = frozenset(
    {"version", "features", "default-features", "default_features", "optional", "workspace"}
)
_NPM_TABLES = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
# Why: a version range never holds these; a URL, a path, a git reference, an npm alias or a
# workspace link does, and each can stand a fork in for a gate tool.
_NOT_A_RANGE = re.compile(r"[:/\\#@]")
_RELEASE = re.compile(r"\d+(?:\.\d+)*")
_DEFAULT_REGISTRIES = frozenset(
    {
        "https://pypi.org/simple",
        "registry+https://github.com/rust-lang/crates.io-index",
        "sparse+https://index.crates.io/",
        "https://registry.npmjs.org",
    }
)

Data = dict[str, object]


@dataclass(frozen=True)
class Change:
    """One dependency added to a manifest, or one package's locked versions changed."""

    name: str
    table: str
    old: str | None
    new: str | None

    def __str__(self) -> str:
        """``name (table) old -> new``, with ``(none)`` for a side that has no entry."""
        return f"{self.name} ({self.table}) {self.old or '(none)'} -> {self.new or '(none)'}"


@dataclass(frozen=True)
class Artifact:
    """One locked package version: where it comes from and what identifies its contents."""

    kind: str
    source: str
    identity: str


class NotDependencyOnlyError(ValueError):
    """The change does more than change dependencies; the message says what."""


def normalise(name: str) -> str:
    """A package name as registries compare it (PEP 503; Cargo's ``-`` and ``_`` alike)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _table(data: Data, key: str) -> Data:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _no_duplicates(pairs: list[tuple[str, object]]) -> Data:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        msg = "a key is given twice"
        raise NotDependencyOnlyError(msg)
    return dict(pairs)


def _load_json(text: str) -> object:
    return json.loads(text, object_pairs_hook=_no_duplicates)


Entries = dict[tuple[str, str], str]


def _add(entries: Entries, table: str, *, name: str, spec: str) -> None:
    key = (table, normalise(name))
    if key in entries:
        msg = f"{name} is listed twice in {table}"
        raise NotDependencyOnlyError(msg)
    entries[key] = spec


def _requirements(entries: Entries, items: object, table: str) -> None:
    for item in items if isinstance(items, list) else []:
        match = _REQUIREMENT_NAME.match(item) if isinstance(item, str) else None
        if match is None:
            msg = f"{table} holds {json.dumps(item)}, not a requirement"
            raise NotDependencyOnlyError(msg)
        _add(entries, table, name=match.group(1), spec=item)


def _pyproject(data: Data) -> tuple[Data, Entries]:
    project = dict(_table(data, "project"))
    entries: Entries = {}
    _requirements(entries, project.pop("dependencies", None), "project.dependencies")
    for extra, items in _table(project, "optional-dependencies").items():
        _requirements(entries, items, f"optional-dependencies.{extra}")
    project.pop("optional-dependencies", None)
    project.pop("version", None)
    for group, items in _table(data, "dependency-groups").items():
        _requirements(entries, items, f"dependency-groups.{group}")
    rest = {k: v for k, v in data.items() if k not in {"project", "dependency-groups"}}
    return rest | {"project": project}, entries


def _cargo_spec(spec: object) -> str:
    """A version string as it is; a table as JSON, or as ``<keys>`` when a key names a source."""
    if isinstance(spec, dict) and not spec.keys() <= _CARGO_KEYS:
        return "<" + ", ".join(sorted(spec)) + ">"
    return spec if isinstance(spec, str) else json.dumps(spec, sort_keys=True)


def _crates(entries: Entries, table: Data, prefix: str) -> None:
    for name, spec in table.items():
        _add(entries, prefix, name=name, spec=_cargo_spec(spec))


def _cargo(data: Data) -> tuple[Data, Entries]:
    rest = dict(data)
    entries: Entries = {}
    for key in _CARGO_TABLES:
        _crates(entries, _table(data, key), key)
        rest.pop(key, None)
    targets = {}
    for target, table in _table(data, "target").items():
        kept = dict(table) if isinstance(table, dict) else {}
        for key in _CARGO_TABLES:
            _crates(entries, _table(kept, key), f"target.{target}.{key}")
            kept.pop(key, None)
        targets[target] = kept
    rest["target"] = targets
    workspace = dict(_table(data, "workspace"))
    _crates(entries, _table(workspace, "dependencies"), "workspace.dependencies")
    workspace.pop("dependencies", None)
    rest["workspace"] = workspace
    package = dict(_table(data, "package"))
    package.pop("version", None)
    rest["package"] = package
    return rest, entries


def _package_json(data: Data) -> tuple[Data, Entries]:
    rest = {k: v for k, v in data.items() if k not in {*_NPM_TABLES, "version"}}
    entries: Entries = {}
    for key in _NPM_TABLES:
        for name, spec in _table(data, key).items():
            _add(entries, key, name=name, spec=spec if isinstance(spec, str) else json.dumps(spec))
    return rest, entries


def _registry_spec(file: str, spec: str) -> bool:
    if file == "pyproject.toml":
        return "@" not in spec and "://" not in spec
    if file == "Cargo.toml":
        if spec.startswith("<"):
            return False
        return spec.startswith("{") or _NOT_A_RANGE.search(spec) is None
    return _NOT_A_RANGE.search(spec) is None


Manifest = Callable[[Data], tuple[Data, Entries]]
_MANIFESTS: dict[str, tuple[Callable[[str], object], Manifest]] = {
    "pyproject.toml": (tomllib.loads, _pyproject),
    "Cargo.toml": (tomllib.loads, _cargo),
    "package.json": (_load_json, _package_json),
}


Locked = dict[str, dict[str, Artifact]]


def _identity(item: Data, keys: tuple[str, ...]) -> str:
    return json.dumps({key: item.get(key) for key in keys}, sort_keys=True)


def _uv(item: Data) -> Artifact:
    source = _table(item, "source")
    if set(source) == {"registry"}:
        return Artifact("registry", str(source["registry"]), _identity(item, ("sdist", "wheels")))
    kind = "local" if set(source) <= {"editable", "virtual"} else "direct"
    return Artifact(kind, json.dumps(source, sort_keys=True), "")


def _cargo_lock(item: Data) -> Artifact:
    source = item.get("source")
    if source is None:
        return Artifact("local", "", "")
    text = str(source)
    if text.startswith(("registry+", "sparse+")):
        return Artifact("registry", text, _identity(item, ("checksum",)))
    return Artifact("direct", text, "")


def _poetry(item: Data) -> Artifact:
    source = _table(item, "source")
    if not source:
        return Artifact("registry", "https://pypi.org/simple", _identity(item, ("files",)))
    if source.get("type") == "legacy":
        return Artifact("registry", str(source.get("url")), _identity(item, ("files",)))
    return Artifact("direct", json.dumps(source, sort_keys=True), "")


def _toml_lock(artifact: Callable[[Data], Artifact]) -> Callable[[Data], Locked]:
    def read(data: Data) -> Locked:
        locked: Locked = {}
        packages = data.get("package")
        for item in packages if isinstance(packages, list) else []:
            if isinstance(item, dict) and "name" in item:
                _lock(
                    locked,
                    str(item["name"]),
                    version=str(item.get("version", "")),
                    artifact=artifact(item),
                )
        return locked

    return read


def _npm_lock(data: Data) -> Locked:
    locked: Locked = {}
    for path, item in _table(data, "packages").items():
        if not path or not isinstance(item, dict):
            continue
        name = str(item.get("name") or path.rsplit("node_modules/", 1)[-1])
        resolved = str(item.get("resolved", ""))
        if item.get("link") or "node_modules/" not in path:
            artifact = Artifact("local", resolved, "")
        elif match := re.match(r"https://[^/]+", resolved):
            artifact = Artifact("registry", match.group(0), _identity(item, ("integrity",)))
        else:
            artifact = Artifact("direct", resolved, "")
        _lock(locked, name, version=str(item.get("version", "")), artifact=artifact)
    return locked


def _lock(locked: Locked, name: str, *, version: str, artifact: Artifact) -> None:
    versions = locked.setdefault(normalise(name), {})
    if versions.get(version, artifact) != artifact:
        msg = f"{name} {version} is locked twice with different contents"
        raise NotDependencyOnlyError(msg)
    versions[version] = artifact


_SIDE_BY_SIDE = frozenset({"Cargo.lock", "package-lock.json"})
_LOCKS: dict[str, tuple[Callable[[str], object], Callable[[Data], Locked]]] = {
    "uv.lock": (tomllib.loads, _toml_lock(_uv)),
    "Cargo.lock": (tomllib.loads, _toml_lock(_cargo_lock)),
    "poetry.lock": (tomllib.loads, _toml_lock(_poetry)),
    "package-lock.json": (_load_json, _npm_lock),
}


def file_name(path: str) -> str:
    """The name a path's file has in the tree; a snapshot is named after the file it copies."""
    return PurePosixPath(path).name.rsplit(SNAPSHOT_SEPARATOR, 1)[-1]


def recognised(path: str) -> bool:
    """Whether ``path`` is a dependency manifest or lockfile, or a snapshot of one."""
    name = file_name(path)
    return name in _MANIFESTS or name in LOCKFILES


def _parse(load: Callable[[str], object], text: str) -> Data:
    try:
        data = load(text)
    except NotDependencyOnlyError:
        raise
    except ValueError as error:
        msg = "it does not parse"
        raise NotDependencyOnlyError(msg) from error
    if not isinstance(data, dict):
        msg = "it does not parse"
        raise NotDependencyOnlyError(msg)
    return data


def _manifest(file: str, before: str, after: str) -> list[Change]:
    load, split = _MANIFESTS[file]
    old_rest, old = split(_parse(load, before))
    new_rest, new = split(_parse(load, after))
    if old_rest != new_rest:
        msg = "it changes more than the dependency tables and the package version"
        raise NotDependencyOnlyError(msg)
    found: list[Change] = []
    for (table, name), spec in sorted(new.items()):
        if old.get((table, name)) == spec:
            continue
        if (table, name) in old:
            msg = f"it changes {name} in {table}, which was there before"
            raise NotDependencyOnlyError(msg)
        if not _registry_spec(file, spec):
            msg = f"{name} in {table} is fetched by {spec}, not from the registry by version"
            raise NotDependencyOnlyError(msg)
        found.append(Change(name, table, None, spec))
    if gone := sorted(f"{name} from {table}" for table, name in old.keys() - new.keys()):
        msg = f"it removes {', '.join(gone)}"
        raise NotDependencyOnlyError(msg)
    return found


def _version(text: str) -> tuple[tuple[int, ...], int, str]:
    """A sort key: the release numbers, then pre-release before final before post-release."""
    match = _RELEASE.match(text)
    if match is None:
        return ((), 0, text)
    release = tuple(int(part) for part in match.group(0).split("."))
    while release and release[-1] == 0:
        release = release[:-1]
    suffix = text[match.end() :].lstrip(".-_")
    stage = 1 if not suffix or suffix.startswith("+") else 2 if suffix.startswith("post") else 0
    return (release, stage, suffix)


def _package(
    name: str,
    old: dict[str, Artifact],
    new: dict[str, Artifact],
    *,
    known: set[str],
    side_by_side: bool,
) -> None:
    """Raise unless ``name``'s locked versions only grow or rise, from a registry ``known``.

    Where versions of a package sit ``side_by_side`` (Cargo, npm), only the highest must not
    fall; elsewhere any new version below the old highest is a rollback for some environment.
    """
    for version in old.keys() & new.keys():
        before, after = old[version], new[version]
        if before != after and not (
            before.kind == after.kind == "local" and before.source == after.source
        ):
            was, now = before.source or "local", after.source or "local"
            msg = f"{name} {version} changed in place ({was} -> {now})"
            raise NotDependencyOnlyError(msg)
    highest = max(old, key=_version) if old else None
    for version in sorted(new.keys() - old.keys(), key=_version):
        artifact = new[version]
        if artifact.kind != "registry" or artifact.source not in known:
            where = artifact.source or "a local path"
            msg = f"{name} {version} comes from {where}, not a known registry"
            raise NotDependencyOnlyError(msg)
        if highest is not None and not side_by_side and _version(version) < _version(highest):
            msg = f"{name} falls from {highest} to {version}"
            raise NotDependencyOnlyError(msg)
    if highest is not None and new and _version(max(new, key=_version)) < _version(highest):
        msg = f"{name} falls from {highest} to {max(new, key=_version)}"
        raise NotDependencyOnlyError(msg)


def _lockfile(file: str, before: str, after: str) -> list[Change]:
    load, read = _LOCKS[file]
    old, new = read(_parse(load, before)), read(_parse(load, after))
    known = set(_DEFAULT_REGISTRIES) | {
        a.source for versions in old.values() for a in versions.values() if a.kind == "registry"
    }
    found: list[Change] = []
    for name in sorted(old.keys() | new.keys()):
        was, now = old.get(name, {}), new.get(name, {})
        _package(name, was, now, known=known, side_by_side=file in _SIDE_BY_SIDE)
        if was.keys() != now.keys():
            show = (
                ", ".join(sorted(was, key=_version)) or None,
                ", ".join(sorted(now, key=_version)) or None,
            )
            found.append(Change(name, "lockfile", *show))
    return found


def changes(path: str, before: str | None, after: str | None) -> list[Change]:
    """What the change of ``path`` does to dependencies, when that is all it does.

    Raises ``NotDependencyOnlyError`` with the reason otherwise: a file created or deleted, a format
    this module does not read, a parse failure, or any change it does not count as safe.
    """
    file = file_name(path)
    if before is None or after is None:
        msg = "it is created or deleted"
        raise NotDependencyOnlyError(msg)
    if file in _MANIFESTS:
        return _manifest(file, before, after)
    if file in _LOCKS:
        return _lockfile(file, before, after)
    msg = f"{file} is not read"
    raise NotDependencyOnlyError(msg)
