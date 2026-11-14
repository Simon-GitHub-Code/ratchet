"""What a guard-path change does to dependencies, when that is all it does (ADR 0011, ADR 0044).

A dependency manifest holds gate configuration beside the dependency list, so the whole file is a
guard path, and so is its lockfile. ``guard-diff`` and the merge judge say, for each, what the
change does to dependencies when that is all it does, so the owner reads the versions and the
pipeline can match the added names against the ones its spec named.

This module reads no manifest or lockfile format (ADR 0042). The binding's ``## Dependencies``
section names the manifests and lockfiles of this stack, the registries it accepts and an entry
point, ``just dependencies <dir>``, that whoever bound the stack wrote. Given a directory holding
the changed files at their own paths, the entry point prints records, one per line, tab separated,
in the format this module defines::

    declared<TAB>file<TAB>table<TAB>name<TAB>spec<TAB>origin
    locked<TAB>file<TAB>scope<TAB>name<TAB>version<TAB>order<TAB>origin<TAB>source<TAB>integrity<TAB>sides
    rest<TAB>file<TAB>digest

``declared`` is one dependency a manifest asks for: its table, the spec as the file writes it, and
whether the spec fetches it from a registry by name and version range (``registry``) or by any
other means (``other``). ``locked`` is one resolved package version of a lockfile: its scope (an
inventory of its own, such as the one a tool keeps for itself), its version as written, ``order``
(dot-separated non-negative integers that sort the versions of one name the way its registry
does), where it comes from (``registry``, ``direct`` or ``local``), the source and an integrity
value that identifies its contents, and whether versions of the name sit beside each other in the
scope (``many``) or only one runs (``one``). ``rest`` is a digest of everything else in the file
that can change what installs: the file less its dependency tables, resolved packages and the
package's own version.

The merge base's entry point reads the files at the merge base and at the head, so the change
cannot rewrite the code that judges it, and this module compares the two outputs. A manifest
change is dependency-only when its ``rest`` is equal and every entry that changed is a new one
fetched from a registry by name and version range. A lockfile change is dependency-only when its
``rest`` is equal, no artifact changed in place at the same version, every new artifact comes
from a registry the lockfile already uses or the binding names, and no package's highest version
falls. Each new version must also appear in a line the change added, so an entry point that
reports what the file does not show is refused. Any other change returns the reason it is not:
each is a way to swap a gate tool or roll it back. A change the contract cannot judge (no section,
no entry point, a run that fails or prints what is not a record) is not dependency-only.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import NoReturn

from ratchet_guards import globs

SECTION = "## Dependencies"
SNAPSHOTS = "quality/snapshots/"
SNAPSHOT_SEPARATOR = "__"
ENTRY = "just dependencies"
NONE_PREFIX = "none:"
MIN_REASON = 10
LABELS = ("Manifests", "Lockfiles", "Entry point", "Registries")
MANIFEST, LOCKFILE = "manifest", "lockfile"
REGISTRY, DIRECT, LOCAL = "registry", "direct", "local"
ORIGINS = frozenset({REGISTRY, DIRECT, LOCAL})
DECLARED_ORIGINS = frozenset({REGISTRY, "other"})
MANY, ONE = "many", "one"
ORDER = re.compile(r"\d+(?:\.\d+)*")
BULLET = re.compile(r"^- ([^:\n]+):[ \t]*(.*?)[ \t]*$")
TICKED = re.compile(r"`([^`\n]+)`")
WIDTHS = {"declared": 6, "locked": 10, "rest": 3}

Runner = Callable[[Mapping[str, str]], str]


class NotDependencyOnlyError(ValueError):
    """The change does more than change dependencies; the message says what."""


@dataclass(frozen=True)
class Contract:
    """What a binding says about dependencies: the files, the registries, whether it has an entry.

    ``manifests`` and ``lockfiles`` are globs over tracked files. ``entry`` is whether the binding
    names the entry point. ``problems`` is what keeps the section from being read as written.
    """

    manifests: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()
    registries: frozenset[str] = frozenset()
    entry: bool = False
    problems: tuple[str, ...] = field(default=())

    def original(self, path: str) -> str | None:
        """The declared file ``path`` is or copies (a snapshot is named after it), else ``None``."""
        name = path
        if path.startswith(SNAPSHOTS) and "/" not in path.removeprefix(SNAPSHOTS):
            name = path.removeprefix(SNAPSHOTS).replace(SNAPSHOT_SEPARATOR, "/")
        # Why: the entry point is given the file at this path under a directory of its own.
        if ".." in name.split("/") or name.startswith("/"):
            return None
        return name if self.kind(name) else None

    def kind(self, path: str) -> str | None:
        """``manifest`` or ``lockfile`` when a glob of the section matches ``path``."""
        for kind, patterns in ((MANIFEST, self.manifests), (LOCKFILE, self.lockfiles)):
            if any(globs.matches(path, pattern) for pattern in patterns):
                return kind
        return None

    def is_lockfile(self, path: str) -> bool:
        """Whether ``path`` is a declared lockfile, or a snapshot of one."""
        file = self.original(path)
        return file is not None and self.kind(file) == LOCKFILE

    def recognised(self, path: str) -> bool:
        """Whether ``path`` is a declared manifest or lockfile, or a snapshot of one."""
        return self.original(path) is not None


def _fields(binding: str) -> dict[str, str] | None:
    """The ``- Label: value`` lines under the section's heading, or ``None`` without one."""
    lines = binding.split("\n")
    start = next((i for i, line in enumerate(lines) if line.rstrip() == SECTION), None)
    if start is None:
        return None
    found: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if match := BULLET.match(line):
            found.setdefault(match.group(1).strip(), match.group(2))
    return found


def _paths(label: str, value: str, problems: list[str]) -> tuple[str, ...]:
    plain = value.strip("` ")
    if not value or value.startswith("<"):
        problems.append(f"'- {label}:' is still the template's: name files, or `none: <reason>`")
    elif plain.lower().startswith(NONE_PREFIX):
        if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            problems.append(f"'- {label}: none:' needs the reason this stack has none")
    elif not (found := tuple(item.strip() for item in TICKED.findall(value))):
        problems.append(f"'- {label}:' names its files in backticks, or says `none: <reason>`")
    else:
        return found
    return ()


def parse(binding: str | None) -> Contract | None:
    """The binding's Dependencies section as a contract, or ``None`` when it has none."""
    fields = _fields(binding) if binding is not None else None
    if fields is None:
        return None
    problems: list[str] = []
    manifests = _paths(LABELS[0], fields.get(LABELS[0], ""), problems)
    lockfiles = _paths(LABELS[1], fields.get(LABELS[1], ""), problems)
    entry = fields.get(LABELS[2], "")
    plain = entry.strip("` ")
    if plain == ENTRY:
        named = True
    else:
        named = False
        reason = plain[len(NONE_PREFIX) :].strip()
        if not plain.lower().startswith(NONE_PREFIX) or len(reason) < MIN_REASON:
            problems.append(f"'- {LABELS[2]}:' is `{ENTRY}`, or `none: <reason>` of its own")
    said = fields.get(LABELS[3], "")
    if not said or said.startswith("<"):
        problems.append(f"'- {LABELS[3]}:' is still the template's: name registries, or `none`")
    registries = frozenset(
        TICKED.findall(said) if not said.strip("` ").lower().startswith("none") else ()
    )
    return Contract(manifests, lockfiles, registries, named, tuple(problems))


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

    origin: str
    source: str
    integrity: str


@dataclass(frozen=True)
class Locked:
    """One ``locked`` record, less the file it was read from."""

    scope: str
    name: str
    version: str
    order: tuple[int, ...]
    artifact: Artifact
    many: bool


@dataclass(frozen=True)
class Declared:
    """One ``declared`` record, less the file it was read from."""

    table: str
    name: str
    spec: str
    origin: str


@dataclass
class Output:
    """The records an entry point printed, by the file they describe."""

    declared: dict[str, list[Declared]] = field(default_factory=dict)
    locked: dict[str, list[Locked]] = field(default_factory=dict)
    rest: dict[str, str] = field(default_factory=dict)


def _refuse(message: str) -> NoReturn:
    raise NotDependencyOnlyError(message)


def _record(output: Output, fields: list[str], line: str) -> None:
    kind, file = fields[0], fields[1]
    if kind == "rest":
        if file in output.rest:
            _refuse(f"the entry point prints two rest lines for {file}")
        output.rest[file] = fields[2]
    elif kind == "declared":
        table, name, spec, origin = fields[2:]
        if origin not in DECLARED_ORIGINS:
            _refuse(f"the entry point prints the origin '{origin}' for a declared entry")
        output.declared.setdefault(file, []).append(Declared(table, name, spec, origin))
    else:
        scope, name, version, order, origin, source, integrity, sides = fields[2:]
        if origin not in ORIGINS or sides not in {MANY, ONE} or not ORDER.fullmatch(order):
            _refuse(f"the entry point prints a locked line it cannot be read from: {line!r}")
        if origin != LOCAL and not integrity:
            _refuse(f"{name} {version} has no integrity, so it cannot be shown unswapped")
        artifact = Artifact(origin, source, integrity)
        parts = tuple(int(part) for part in order.split("."))
        output.locked.setdefault(file, []).append(
            Locked(scope, name, version, parts, artifact, sides == MANY)
        )


def read_output(text: str) -> Output:
    """The records of an entry point's output; anything that is not one is refused."""
    output = Output()
    for line in text.split("\n"):
        if not line.strip():
            continue
        fields = line.split("\t")
        if fields[0] not in WIDTHS or len(fields) != WIDTHS[fields[0]]:
            _refuse(f"the entry point prints a line that is not a record: {line[:80]!r}")
        _record(output, fields, line)
    return output


Entries = dict[tuple[str, str], Declared]


def _declared(output: Output, file: str) -> Entries:
    entries: Entries = {}
    for item in output.declared.get(file, []):
        key = (item.table, item.name)
        if key in entries:
            _refuse(f"{item.name} is listed twice in {item.table}")
        entries[key] = item
    return entries


def _same_rest(file: str, before: Output, after: Output) -> None:
    if file not in before.rest or file not in after.rest:
        _refuse("the entry point prints no rest line for it")
    if before.rest[file] != after.rest[file]:
        _refuse("it changes more than the dependency tables and the package version")


def _manifest(file: str, before: Output, after: Output, *, added: str) -> list[Change]:
    _same_rest(file, before, after)
    old, new = _declared(before, file), _declared(after, file)
    found: list[Change] = []
    for (table, name), item in sorted(new.items()):
        if old.get((table, name)) == item:
            continue
        if (table, name) in old:
            _refuse(f"it changes {name} in {table}, which was there before")
        if item.origin != REGISTRY:
            _refuse(
                f"{name} in {table} is fetched by {item.spec}, not from the registry by version"
            )
        _shown(item.spec, name, added)
        found.append(Change(name, table, None, item.spec))
    if gone := sorted(f"{name} from {table}" for table, name in old.keys() - new.keys()):
        _refuse(f"it removes {', '.join(gone)}")
    return found


def _shown(text: str, name: str, added: str) -> None:
    """Refuse an entry point that reports what no line of the change shows."""
    if text not in added:
        _refuse(f"the entry point reports '{text}' for {name}, which no added line shows")


Versions = dict[str, tuple[Artifact, tuple[int, ...]]]
Inventory = dict[tuple[str, str], tuple[Versions, bool]]


def _inventory(output: Output, file: str) -> Inventory:
    found: Inventory = {}
    for item in output.locked.get(file, []):
        versions, many = found.setdefault((item.scope, item.name), ({}, item.many))
        if many != item.many:
            _refuse(f"{item.name} is given both as one version and as many in {item.scope}")
        known = versions.get(item.version)
        if known is not None and known[0] != item.artifact:
            _refuse(f"{item.name} {item.version} is locked twice with different contents")
        versions[item.version] = (item.artifact, item.order)
    return found


def _package(
    name: str, versions: tuple[Versions, Versions], *, known: set[str], many: bool, added: str
) -> None:
    """Raise unless ``name``'s locked versions only grow or rise, from a registry ``known``.

    Where versions of a package sit beside each other (``many``), only the highest must not fall;
    elsewhere any new version below the old highest is a rollback for some environment.
    """
    old, new = versions
    for version in old.keys() & new.keys():
        before, after = old[version][0], new[version][0]
        if before != after and not (
            before.origin == after.origin == LOCAL and before.source == after.source
        ):
            was, now = before.source or LOCAL, after.source or LOCAL
            _refuse(f"{name} {version} changed in place ({was} -> {now})")
    highest = max(old, key=lambda v: old[v][1]) if old else None
    for version in sorted(new.keys() - old.keys(), key=lambda v: new[v][1]):
        artifact = new[version][0]
        if artifact.origin != REGISTRY or artifact.source not in known:
            where = artifact.source or "a local path"
            _refuse(f"{name} {version} comes from {where}, not a known registry")
        if highest is not None and not many and new[version][1] < old[highest][1]:
            _refuse(f"{name} falls from {highest} to {version}")
        _shown(version, name, added)
    if highest is not None and new:
        top = max(new, key=lambda v: new[v][1])
        if new[top][1] < old[highest][1]:
            _refuse(f"{name} falls from {highest} to {top}")


def _lockfile(
    file: str, before: Output, after: Output, *, added: str, accepted: frozenset[str]
) -> list[Change]:
    _same_rest(file, before, after)
    old, new = _inventory(before, file), _inventory(after, file)
    known = set(accepted) | {
        artifact.source
        for versions, _ in old.values()
        for artifact, _ in versions.values()
        if artifact.origin == REGISTRY
    }
    found: list[Change] = []
    for scope, name in sorted(old.keys() | new.keys()):
        was, many = old.get((scope, name), ({}, False))
        now, now_many = new.get((scope, name), ({}, many))
        _package(name, (was, now), known=known, many=many and now_many, added=added)
        if was.keys() != now.keys():
            show = (
                ", ".join(sorted(was, key=lambda v: was[v][1])) or None,
                ", ".join(sorted(now, key=lambda v: now[v][1])) or None,
            )
            found.append(Change(name, scope, *show))
    return found


def _added(before: str, after: str) -> str:
    """The lines of ``after`` that ``before`` lacks."""
    seen = set(before.split("\n"))
    return "\n".join(line for line in after.split("\n") if line not in seen)


Verdict = list[Change] | NotDependencyOnlyError
Texts = Mapping[str, tuple[str | None, str | None]]


def _verdict(
    contract: Contract, file: str, outputs: tuple[Output, Output], *, texts: tuple[str, str]
) -> Verdict:
    old, new = outputs
    added = _added(*texts)
    try:
        if contract.kind(file) == MANIFEST:
            return _manifest(file, old, new, added=added)
        return _lockfile(file, old, new, added=added, accepted=contract.registries)
    except NotDependencyOnlyError as reason:
        return reason


def judge(contract: Contract, changed: Texts, run: Runner) -> dict[str, Verdict]:
    """What each changed path does to dependencies, for the paths the contract recognises.

    ``changed`` maps a path to its text before and after (``None`` for a side where it is not
    there). ``run`` is the merge base's entry point: given files by path, the text it prints. It is
    called once for each side, and not at all when no path can be judged. A path with no verdict
    is not a dependency file.
    """
    verdicts: dict[str, Verdict] = {}
    pending: dict[str, tuple[str, str]] = {}
    owners: dict[str, list[str]] = {}
    for path, (before, after) in changed.items():
        file = contract.original(path)
        if file is None:
            continue
        if not contract.entry:
            verdicts[path] = NotDependencyOnlyError(
                "the binding names no entry point to read dependencies with"
            )
        elif before is None or after is None:
            verdicts[path] = NotDependencyOnlyError("it is created or deleted")
        elif pending.setdefault(file, (before, after)) != (before, after):
            verdicts[path] = NotDependencyOnlyError(f"it differs from {file}, the file it copies")
        else:
            owners.setdefault(file, []).append(path)
    if not pending:
        return verdicts
    try:
        old = read_output(run({file: texts[0] for file, texts in pending.items()}))
        new = read_output(run({file: texts[1] for file, texts in pending.items()}))
    except NotDependencyOnlyError as reason:
        return verdicts | {path: reason for paths in owners.values() for path in paths}
    for file, texts in pending.items():
        verdicts |= dict.fromkeys(owners[file], _verdict(contract, file, (old, new), texts=texts))
    return verdicts
