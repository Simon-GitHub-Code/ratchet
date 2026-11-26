#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

REGISTRY = Path(__file__).resolve().with_name("companions.json")
MARKETPLACE = Path(__file__).resolve().parents[1] / ".claude-plugin/marketplace.json"
OWNER = "owner"
AGENT = "agent"
PIPELINE_ROLES = ("tdd", "codebase_design", "code_review", "ui_design")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_HEADER_BYTES = 4096


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    plugin: str
    slug: str
    kind: str
    fallback: str
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillMeta:
    slug: str
    description: str
    user_only: bool


@dataclass(frozen=True, slots=True)
class Resolution:
    role: str
    plugin: str
    slug: str | None
    kind: str
    text: str
    drifted: bool = False
    note: str = ""


@functools.lru_cache(maxsize=1)
def declared_plugins() -> dict[str, str]:
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for entry in data.get("plugins", []):
        for dep in entry.get("dependencies", []):
            plugin, _, market = str(dep).partition("@")
            found[plugin] = market
    return found


@functools.lru_cache(maxsize=1)
def roles() -> dict[str, Role]:
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {
        name: Role(
            name=name,
            plugin=entry["plugin"],
            slug=entry["slug"],
            kind=entry["kind"],
            fallback=entry["fallback"],
            signals=tuple(s.lower() for s in entry["signals"]),
        )
        for name, entry in raw.items()
    }


def _version_key(path: Path) -> tuple[tuple[int, str], ...]:
    parts = re.split(r"([0-9]+)", path.name)
    return tuple((int(part), "") if part.isdigit() else (-1, part) for part in parts)


def _claude_root(home: Path, plugin: str, marketplace: str) -> Path | None:
    folder = home / ".claude/plugins/cache" / marketplace / plugin
    if not folder.is_dir():
        return None
    versions = [
        child for child in folder.iterdir() if child.is_dir() and (child / "skills").is_dir()
    ]
    return max(versions, key=_version_key) if versions else None


def plugin_root(
    plugin: str, *, home: Path | None = None, overrides: Mapping[str, Path] | None = None
) -> Path | None:
    if overrides and plugin in overrides:
        chosen = overrides[plugin]
        return chosen if (chosen / "skills").is_dir() else None
    base = home or Path.home()
    market = declared_plugins().get(plugin, "")
    candidates = [
        base / ".gemini/config/plugins" / plugin,
        *([found] if market and (found := _claude_root(base, plugin, market)) else []),
        base / ".codex/plugins" / plugin,
        base / ".grok/installed-plugins" / plugin,
        base / ".config/opencode/plugins" / plugin,
    ]
    return next((path for path in candidates if (path / "skills").is_dir()), None)


def read_skill(path: Path) -> SkillMeta:
    with path.open(encoding="utf-8", errors="replace") as handle:
        head = handle.read(_HEADER_BYTES)
    matched = _FRONTMATTER.match(head)
    fields: dict[str, str] = {}
    if matched:
        for line in matched.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip().strip('"').strip("'")
    return SkillMeta(
        slug=fields.get("name") or path.parent.name,
        description=fields.get("description", ""),
        user_only=fields.get("disable-model-invocation", "").lower() == "true",
    )


def find_slug(root: Path, slug: str) -> SkillMeta | None:
    skills_dir = root / "skills"
    direct = skills_dir / slug / "SKILL.md"
    if direct.is_file():
        return read_skill(direct)
    for child in sorted(skills_dir.iterdir()):
        nested = child / slug / "SKILL.md"
        if child.is_dir() and nested.is_file():
            return read_skill(nested)
    return None


def scan_skills(root: Path) -> tuple[SkillMeta, ...]:
    return tuple(read_skill(path) for path in sorted((root / "skills").rglob("SKILL.md")))


def _format(plugin: str, slug: str, kind: str) -> str:
    return f"/{plugin}:{slug}" if kind == OWNER else f"{plugin}:{slug}"


def _match_renamed(role: Role, root: Path) -> Resolution:
    candidates = [
        skill
        for skill in scan_skills(root)
        if all(signal in skill.description.lower() for signal in role.signals)
        and (role.kind == OWNER or not skill.user_only)
    ]
    if len(candidates) == 1:
        chosen = candidates[0]
        return Resolution(
            role=role.name,
            plugin=role.plugin,
            slug=chosen.slug,
            kind=role.kind,
            text=_format(role.plugin, chosen.slug, role.kind),
            drifted=True,
            note=f"{role.plugin}:{role.slug} missing; resolved to {role.plugin}:{chosen.slug}",
        )
    return Resolution(
        role=role.name,
        plugin=role.plugin,
        slug=None,
        kind=role.kind,
        text=role.fallback,
        drifted=True,
        note=f"{role.plugin}:{role.slug} not found in {root}",
    )


def resolve(
    role_name: str, *, home: Path | None = None, overrides: Mapping[str, Path] | None = None
) -> Resolution:
    role = roles()[role_name]
    root = plugin_root(role.plugin, home=home, overrides=overrides)
    if root is None:
        return Resolution(
            role=role.name,
            plugin=role.plugin,
            slug=role.slug,
            kind=role.kind,
            text=_format(role.plugin, role.slug, role.kind),
        )
    direct = find_slug(root, role.slug)
    if direct is not None:
        note = (
            f"{role.plugin}:{direct.slug} is marked disable-model-invocation: true"
            if role.kind == AGENT and direct.user_only
            else ""
        )
        return Resolution(
            role=role.name,
            plugin=role.plugin,
            slug=direct.slug,
            kind=role.kind,
            text=_format(role.plugin, direct.slug, role.kind),
            drifted=bool(note),
            note=note,
        )
    return _match_renamed(role, root)


def ref(
    role_name: str, *, home: Path | None = None, overrides: Mapping[str, Path] | None = None
) -> str:
    return resolve(role_name, home=home, overrides=overrides).text


def pipeline_map(
    *, home: Path | None = None, overrides: Mapping[str, Path] | None = None
) -> dict[str, str]:
    return {name: ref(name, home=home, overrides=overrides) for name in PIPELINE_ROLES}


def check_all(
    *,
    home: Path | None = None,
    overrides: Mapping[str, Path] | None = None,
    require_installed: bool = False,
) -> list[str]:
    problems: list[str] = []
    seen_missing: set[str] = set()
    for role in roles().values():
        root = plugin_root(role.plugin, home=home, overrides=overrides)
        if root is None:
            if require_installed and role.plugin not in seen_missing:
                seen_missing.add(role.plugin)
                problems.append(f"{role.plugin}: plugin not installed")
            continue
        result = resolve(role.name, home=home, overrides=overrides)
        if result.drifted:
            problems.append(f"{role.name}: {result.note}")
    return problems


def parse_plugin_dirs(values: Sequence[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for item in values:
        name, sep, raw = item.partition("=")
        if not sep or not name.strip() or not raw.strip():
            msg = f"expected NAME=PATH, got {item!r}"
            raise ValueError(msg)
        found[name.strip()] = Path(raw.strip()).expanduser().resolve()
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-installed", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--home", type=Path)
    parser.add_argument("--plugin-dir", action="append", default=[])
    options = parser.parse_args(argv[1:])
    try:
        overrides = parse_plugin_dirs(options.plugin_dir)
    except ValueError as error:
        print(f"companions: {error}", file=sys.stderr)
        return 2
    if options.as_json:
        resolved = {name: ref(name, home=options.home, overrides=overrides) for name in roles()}
        print(json.dumps(resolved, indent=2, sort_keys=True))
        return 0
    problems = check_all(
        home=options.home, overrides=overrides, require_installed=options.require_installed
    )
    for problem in problems:
        print(f"companions: {problem}")
    if problems:
        return 1
    if options.check:
        print("companions: green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
