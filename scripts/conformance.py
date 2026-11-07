#!/usr/bin/env python3
"""Method conformance gate: does a repository still carry the Ratchet method's artefacts?

Usage: ``conformance.py [ROOT]`` exits 1 and prints one line per failure. ``conformance.py
--self-test`` builds repositories from the adopt templates, expects the pristine one to pass
and each mutated one to fail, so the checker is shown to bite.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path

REQUIRED_RECIPES = (
    "tier0",
    "tier1",
    "tier2",
    "check",
    "check-deep",
    "ratchet",
    "ratchet-lowered",
    "snapshots",
    "snapshots-check",
    "suppressions",
    "guard-diff",
    "conformance",
    "reports",
)
BINDING_RECIPES = (
    "tier0",
    "tier1",
    "tier2",
    "check",
    "check-deep",
    "ratchet-lowered",
    "snapshots-check",
    "suppressions",
    "guard-diff",
    "conformance",
)
ADR_STATUSES = {"proposed", "accepted", "superseded", "rejected"}
DISPUTE_STATUSES = {"open", "upheld", "rejected"}
DISPUTE_SECTIONS = (
    "## Gate",
    "## Exact failure",
    "## Why the gate is wrong",
    "## Options",
    "## Recommendation",
)
BRIEF_SECTIONS = (
    "## Problem",
    "## Conjecture about the solution",
    "## Out of scope",
    "## Glossary terms",
    "## Acceptance sketch",
)
PR_SECTIONS = (
    "## Conjecture",
    "## Brief and spec",
    "## Criticism",
    "## Guard paths",
    "## Dependencies",
    "## Decisions",
    "## Gates",
)
LOCKED_PATTERNS = {".claude/**", "guards/**"}
RECIPE_LINE = re.compile(r"^([A-Za-z0-9_-]+)(?:\s+[^:=]*)?:(?!=)")
NUMBERED = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
MIN_REASON = 10
MIN_REGISTER_REASON = 20

Check = Callable[[Path], Iterable[str]]


def read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def check_claude_md(root: Path) -> Iterable[str]:
    text = read(root / "CLAUDE.md")
    if text is None:
        yield "CLAUDE.md: missing"
    elif "ratchet:method" not in text:
        yield "CLAUDE.md: does not tell agents to load the ratchet:method skill"


def check_glossary(root: Path) -> Iterable[str]:
    text = read(root / "GLOSSARY.md")
    if text is None:
        yield "GLOSSARY.md: missing"
    elif "## Language" not in text:
        yield "GLOSSARY.md: has no '## Language' section"


def check_brief(root: Path) -> Iterable[str]:
    text = read(root / "docs/design-brief.md")
    if text is None:
        yield "docs/design-brief.md: missing"
        return
    for heading in ("## Slice one", "## Explicitly out"):
        if heading not in text:
            yield f"docs/design-brief.md: has no '{heading}' section"


def numbered_files(directory: Path) -> list[tuple[int, Path]]:
    found = []
    for path in sorted(directory.glob("*.md")):
        match = NUMBERED.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return found


def check_adrs(root: Path) -> Iterable[str]:
    directory = root / "docs/adr"
    if not directory.is_dir():
        yield "docs/adr: missing"
        return
    files = numbered_files(directory)
    if not files:
        yield "docs/adr: no NNNN-<slug>.md record"
        return
    numbers = [number for number, _ in files]
    if numbers != list(range(1, len(numbers) + 1)):
        yield f"docs/adr: numbering has gaps or duplicates: {numbers}"
    for number, path in files:
        text = path.read_text(encoding="utf-8")
        match = re.match(r"---\nstatus: (\S+)\n(?:superseded-by: (\d{4})\n)?---\n# \S", text)
        if match is None:
            rel = path.relative_to(root)
            yield f"{rel}: needs front matter 'status: <value>' then a '# ' title"
            continue
        status, superseded_by = match.group(1), match.group(2)
        if status not in ADR_STATUSES:
            yield f"{path.relative_to(root)}: status '{status}' not in {sorted(ADR_STATUSES)}"
        if status == "superseded" and superseded_by is None:
            yield f"{path.relative_to(root)}: a superseded ADR carries 'superseded-by: NNNN'"
        if number == 1 and "Ratchet method" not in text:
            yield f"{path.relative_to(root)}: ADR 0001 records adopting the Ratchet method"


def check_binding(root: Path) -> Iterable[str]:
    text = read(root / "docs/process/binding.md")
    if text is None:
        yield "docs/process/binding.md: missing"
        return
    for recipe in BINDING_RECIPES:
        if f"`just {recipe}`" not in text:
            yield f"docs/process/binding.md: does not name `just {recipe}`"


def check_manifest(root: Path) -> Iterable[str]:
    path = root / "guards/manifest.toml"
    if not path.is_file():
        yield "guards/manifest.toml: missing"
        return
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        yield f"guards/manifest.toml: {error}"
        return
    groups = manifest.get("group")
    if not isinstance(groups, list) or not groups:
        yield "guards/manifest.toml: has no [[group]]"
        return
    locked: set[str] = set()
    for group in groups:
        name = group.get("name", "<unnamed>")
        if len(str(group.get("reason", ""))) < MIN_REASON:
            yield f"guards/manifest.toml: group '{name}' needs a reason of {MIN_REASON}+ characters"
        if not isinstance(group.get("paths"), list):
            yield f"guards/manifest.toml: group '{name}' needs a paths list"
        elif group.get("edit") == "deny":
            locked.update(group["paths"])
    for pattern in sorted(LOCKED_PATTERNS - locked):
        yield f"guards/manifest.toml: '{pattern}' is not in an edit = \"deny\" group"


def check_quality(root: Path) -> Iterable[str]:
    for name in ("thresholds", "mutation-register"):
        path = root / "quality" / f"{name}.toml"
        if not path.is_file():
            yield f"quality/{name}.toml: missing"
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            yield f"quality/{name}.toml: {error}"
            continue
        if name == "mutation-register":
            yield from check_register(data)
    if not (root / "quality/snapshots").is_dir():
        yield "quality/snapshots: missing"


def check_register(data: dict[str, object]) -> Iterable[str]:
    survivors = data.get("survivor", [])
    if not isinstance(survivors, list):
        yield "quality/mutation-register.toml: 'survivor' is not an array of tables"
        return
    for index, entry in enumerate(survivors):
        for key in ("tool", "identity", "reason", "date"):
            if key not in entry:
                yield f"quality/mutation-register.toml: survivor {index} lacks '{key}'"
        if len(str(entry.get("reason", ""))) < MIN_REGISTER_REASON:
            yield (
                f"quality/mutation-register.toml: survivor {index} needs a reason of "
                f"{MIN_REGISTER_REASON}+ characters"
            )


def check_justfile(root: Path) -> Iterable[str]:
    text = read(root / "justfile")
    if text is None:
        yield "justfile: missing"
        return
    recipes = {match.group(1) for line in text.splitlines() if (match := RECIPE_LINE.match(line))}
    for recipe in REQUIRED_RECIPES:
        if recipe not in recipes:
            yield f"justfile: has no recipe '{recipe}'"


def check_disputes(root: Path) -> Iterable[str]:
    directory = root / "docs/disputes"
    if not directory.is_dir():
        yield "docs/disputes: missing"
        return
    for _, path in numbered_files(directory):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^- Status: (\S+)$", text, re.MULTILINE)
        if match is None or match.group(1) not in DISPUTE_STATUSES:
            yield f"{path.relative_to(root)}: needs '- Status: open|upheld|rejected'"
        for section in DISPUTE_SECTIONS:
            if section not in text:
                yield f"{path.relative_to(root)}: has no '{section}' section"


def check_features(root: Path) -> Iterable[str]:
    directory = root / "docs/features"
    if not directory.is_dir():
        yield "docs/features: missing"
        return
    for brief in sorted(directory.glob("*/brief.md")):
        text = brief.read_text(encoding="utf-8")
        for section in BRIEF_SECTIONS:
            if section not in text:
                yield f"{brief.relative_to(root)}: has no '{section}' section"


def check_pr_template(root: Path) -> Iterable[str]:
    text = read(root / ".github/pull_request_template.md")
    if text is None:
        yield ".github/pull_request_template.md: missing"
        return
    for section in PR_SECTIONS:
        if not re.search(rf"^{re.escape(section)}\s*$", text, re.MULTILINE):
            yield f".github/pull_request_template.md: has no '{section}' section"


CHECKS: tuple[Check, ...] = (
    check_claude_md,
    check_glossary,
    check_brief,
    check_adrs,
    check_binding,
    check_manifest,
    check_quality,
    check_justfile,
    check_disputes,
    check_features,
    check_pr_template,
)


def failures(root: Path) -> list[str]:
    return [failure for check in CHECKS for failure in check(root)]


def run(root: Path) -> int:
    found = failures(root)
    for line in found:
        print(line)
    print(f"conformance: {'red' if found else 'green'} ({len(found)} failures)")
    return 1 if found else 0


Mutation = tuple[str, Callable[[Path], None]]


def mutations() -> list[Mutation]:
    def strip_stanza(root: Path) -> None:
        path = root / "CLAUDE.md"
        path.write_text(path.read_text().replace("ratchet:method", "nothing"))

    def drop_recipe(root: Path) -> None:
        path = root / "justfile"
        path.write_text(path.read_text().replace("\nconformance:", "\nconform:"))

    def unlock_harness(root: Path) -> None:
        path = root / "guards/manifest.toml"
        path.write_text(path.read_text().replace('edit = "deny"', 'edit = "ask"'))

    def gap_adrs(root: Path) -> None:
        (root / "docs/adr/0003-gap.md").write_text("---\nstatus: accepted\n---\n# A gap\n")

    def bad_dispute(root: Path) -> None:
        (root / "docs/disputes/0001-bad.md").write_text("# 0001: bad\n\n- Status: maybe\n")

    def short_register_reason(root: Path) -> None:
        path = root / "quality/mutation-register.toml"
        path.write_text(
            path.read_text()
            + '\n[[survivor]]\ntool = "x"\nidentity = "y"\nreason = "short"\ndate = 2026-01-01\n'
        )

    def drop_pr_section(root: Path) -> None:
        path = root / ".github/pull_request_template.md"
        path.write_text(path.read_text().replace("## Guard paths", "## Guards"))

    return [
        ("CLAUDE.md without the method stanza", strip_stanza),
        ("justfile without the conformance recipe", drop_recipe),
        ("manifest with the harness unlocked", unlock_harness),
        ("ADR numbering with a gap", gap_adrs),
        ("dispute with an unknown status", bad_dispute),
        ("register entry with a short reason", short_register_reason),
        ("pull-request template missing a section", drop_pr_section),
    ]


def self_test(templates: Path) -> int:
    with tempfile.TemporaryDirectory() as scratch:
        pristine = Path(scratch) / "pristine"
        shutil.copytree(templates, pristine)
        found = failures(pristine)
        if found:
            print("self-test: the pristine templates fail:")
            for line in found:
                print(f"  {line}")
            return 1
        print("self-test: pristine templates pass")
        broken = 0
        for label, mutate in mutations():
            copy = Path(scratch) / re.sub(r"\W+", "-", label)
            shutil.copytree(templates, copy)
            mutate(copy)
            bites = bool(failures(copy))
            print(f"self-test: {label}: {'bites' if bites else 'DOES NOT BITE'}")
            broken += 0 if bites else 1
    return 1 if broken else 0


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--self-test"]:
        templates = Path(__file__).resolve().parents[1] / "skills/adopt/templates"
        return self_test(templates)
    root = Path(argv[1]) if len(argv) > 1 else Path()
    return run(root.resolve())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
