#!/usr/bin/env python3
"""Method conformance gate: does a repository still carry the Ratchet method's artefacts?

Usage: ``conformance.py [ROOT]`` exits 1 and prints one line per failure. ``conformance.py
--self-test`` builds repositories from the init and adopt templates, expects the pristine one
to pass and each mutated one to fail, so the checker is shown to bite.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path

ENTRY_POINTS = (
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
    "budget",
)
SPEED_SECTION = "## Speed"
WORKFLOWS = Path(".github/workflows")
CI_SHAPE = (("concurrency:", "cancels superseded runs"), ("cache", "caches between runs"))
ADR_STATUS = re.compile(r"^(proposed|accepted|deprecated|rejected|superseded( by ADR-\d{4})?)$")
ADR_FRONT_MATTER = re.compile(
    r"\A(?:---\nstatus: (?P<status>[^\n]+)\n(?:superseded-by: (?P<by>\d{4})\n)?---\n+)?# \S"
)
ADR_STATUS_WORDS = "proposed|accepted|deprecated|rejected|superseded"
REFUTATION_SECTION = "## Refutation criterion"
CONJECTURE_SECTION = "## Conjecture about the solution"
EMPTY_BODIES = {"none", "n/a", "na", "tbd", "todo", "-", "?"}
MIN_BODY = 20
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
    "## Criticism",
    "## Guard paths",
    "## Decisions",
    "## Gates",
)
LOCKED_PATTERNS = {".claude/**", "guards/**"}
TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|")
PR_TEMPLATE_LINE = re.compile(r"^- Pull request template: `([^`]+)`$", re.MULTILINE)
DEFAULT_PR_TEMPLATE = ".github/pull_request_template.md"
NONE_REASON = re.compile(r"^none\b[\s,:;-]*(.*)$")
NUMBERED = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
MIN_REASON = 10
MIN_REGISTER_REASON = 20

Check = Callable[[Path], Iterable[str]]


def read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def check_agent_instructions(root: Path) -> Iterable[str]:
    text = read(root / "AGENTS.md")
    if text is None:
        text = read(root / "CLAUDE.md")
    if text is None:
        yield "AGENTS.md (or CLAUDE.md): missing"
    elif "ratchet:method" not in text:
        yield "AGENTS.md: does not tell agents to load the ratchet:method skill"


def check_glossary(root: Path) -> Iterable[str]:
    if (root / "GLOSSARY-MAP.md").is_file():
        return
    text = read(root / "GLOSSARY.md")
    if text is None:
        yield "GLOSSARY.md (or GLOSSARY-MAP.md): missing"
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
        yield from check_adr(number, path, path.relative_to(root))


def check_adr(number: int, path: Path, rel: Path) -> Iterable[str]:
    text = path.read_text(encoding="utf-8")
    match = ADR_FRONT_MATTER.match(text)
    if match is None:
        yield f"{rel}: opens with a '# ' title, optionally after 'status:' front matter"
        return
    status, superseded_by = match.group("status"), match.group("by")
    if status is not None and not ADR_STATUS.match(status):
        yield f"{rel}: status '{status}' is not {ADR_STATUS_WORDS}"
    if status == "superseded" and superseded_by is None:
        yield f"{rel}: a superseded ADR names its successor: 'superseded-by: NNNN'"
    if REFUTATION_SECTION not in text:
        yield f"{rel}: has no '{REFUTATION_SECTION}' section"
    elif empty_body(text, REFUTATION_SECTION):
        yield f"{rel}: '{REFUTATION_SECTION}' names no observation that would show it wrong"
    if number == 1 and "Ratchet method" not in text:
        yield f"{rel}: ADR 0001 records adopting the Ratchet method"


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    body = text[start + len(heading) :]
    end = body.find("\n## ")
    return body if end < 0 else body[:end]


def empty_body(text: str, heading: str) -> bool:
    """Whether the section under ``heading`` says nothing: absent, blank, placeholder or short."""
    body = section(text, heading).strip()
    return body.lower().rstrip(".") in EMPTY_BODIES or len(body) < MIN_BODY


def table_cells(body: str) -> list[tuple[str, str]]:
    rows = []
    for line in body.splitlines():
        match = TABLE_ROW.match(line)
        if match and not set(match.group(2)) <= set("-: ") and match.group(1) != "---":
            rows.append((match.group(1), match.group(2)))
    return rows[1:]


def check_binding(root: Path) -> Iterable[str]:
    text = read(root / "docs/process/binding.md")
    if text is None:
        yield "docs/process/binding.md: missing"
        return
    commands = dict(table_cells(section(text, "## Entry points")))
    for name in ENTRY_POINTS:
        command = commands.get(name, "")
        if not command or "<" in command:
            yield f"docs/process/binding.md: entry point '{name}' names no command"
    gates = section(text, "## Gates")
    if not gates:
        yield "docs/process/binding.md: has no '## Gates' section"
    for question, tool in table_cells(gates):
        match = NONE_REASON.match(tool)
        if match and len(match.group(1)) < MIN_REASON:
            yield (
                f"docs/process/binding.md: gate '{question}' is bound to none without a reason "
                f"of {MIN_REASON}+ characters"
            )


def pr_template_path(root: Path) -> str:
    text = read(root / "docs/process/binding.md") or ""
    match = PR_TEMPLATE_LINE.search(text)
    return match.group(1) if match else DEFAULT_PR_TEMPLATE


def check_speed(root: Path) -> Iterable[str]:
    text = read(root / "docs/process/binding.md") or ""
    if text and SPEED_SECTION not in text:
        yield f"docs/process/binding.md: has no '{SPEED_SECTION}' section (ceilings, shards)"
    workflows = root / WORKFLOWS
    if not workflows.is_dir():
        return
    for path in sorted(workflows.glob("*.yml")):
        content = path.read_text(encoding="utf-8")
        for marker, meaning in CI_SHAPE:
            if marker not in content:
                yield f"{path.relative_to(root)}: nothing {meaning} ('{marker}' absent)"


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
        elif not group["paths"]:
            yield f"guards/manifest.toml: group '{name}' has no paths yet; bind or remove it"
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
        for heading in BRIEF_SECTIONS:
            if heading not in text:
                yield f"{brief.relative_to(root)}: has no '{heading}' section"
        if CONJECTURE_SECTION in text and empty_body(text, CONJECTURE_SECTION):
            yield f"{brief.relative_to(root)}: '{CONJECTURE_SECTION}' states no conjecture"


def check_pr_template(root: Path) -> Iterable[str]:
    relative = pr_template_path(root)
    text = read(root / relative)
    if text is None:
        yield f"{relative}: missing (the binding names the pull request template)"
        return
    for heading in PR_SECTIONS:
        if not re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE):
            yield f"{relative}: has no '{heading}' section"


CHECKS: tuple[Check, ...] = (
    check_agent_instructions,
    check_glossary,
    check_brief,
    check_adrs,
    check_binding,
    check_speed,
    check_manifest,
    check_quality,
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
    return [*artefact_mutations(), *record_mutations()]


def artefact_mutations() -> list[Mutation]:
    def strip_stanza(root: Path) -> None:
        path = root / "AGENTS.md"
        path.write_text(path.read_text().replace("ratchet:method", "nothing"))

    def drop_entry_point(root: Path) -> None:
        path = root / "docs/process/binding.md"
        path.write_text(path.read_text().replace("| conformance |", "| conform |"))

    def none_without_reason(root: Path) -> None:
        path = root / "docs/process/binding.md"
        path.write_text(path.read_text().replace("| format | |", "| format | none |"))

    def unlock_harness(root: Path) -> None:
        path = root / "guards/manifest.toml"
        path.write_text(path.read_text().replace('edit = "deny"', 'edit = "ask"'))

    def unbound_group(root: Path) -> None:
        path = root / "guards/manifest.toml"
        path.write_text(path.read_text().replace('paths = [\n    "quality/**",\n]', "paths = []"))

    def drop_pr_section(root: Path) -> None:
        path = root / DEFAULT_PR_TEMPLATE
        path.write_text(path.read_text().replace("## Guard paths", "## Guards"))

    def drop_speed_section(root: Path) -> None:
        path = root / "docs/process/binding.md"
        path.write_text(path.read_text().replace(SPEED_SECTION, "## Slowness"))

    def ci_without_concurrency(root: Path) -> None:
        path = root / WORKFLOWS / "ci.yml"
        path.write_text(path.read_text().replace("concurrency:", "serial:"))

    def ci_without_cache(root: Path) -> None:
        path = root / WORKFLOWS / "deep.yml"
        path.write_text(path.read_text().replace("cache", "store"))

    return [
        ("AGENTS.md without the method stanza", strip_stanza),
        ("binding without the conformance entry point", drop_entry_point),
        ("binding with a gate bound to none and no reason", none_without_reason),
        ("manifest with the harness unlocked", unlock_harness),
        ("manifest with a group still unbound", unbound_group),
        ("pull-request template missing a section", drop_pr_section),
        ("binding without a Speed section", drop_speed_section),
        ("CI workflow without concurrency control", ci_without_concurrency),
        ("CI workflow without a cache", ci_without_cache),
    ]


def record_mutations() -> list[Mutation]:
    def adr_without_refuter(root: Path) -> None:
        (root / "docs/adr/0002-plain.md").write_text("# We keep it plain\n\nBecause.\n")

    def adr_with_an_empty_refuter(root: Path) -> None:
        (root / "docs/adr/0002-empty.md").write_text(
            f"# We keep it empty\n\nBecause.\n\n{REFUTATION_SECTION}\nNone.\n"
        )

    def brief_without_a_conjecture(root: Path) -> None:
        (root / "docs/features/x").mkdir(parents=True)
        headings = "\n\n".join(f"{h}\nSomething substantive here." for h in BRIEF_SECTIONS)
        text = "# X\n\n" + headings.replace(
            f"{CONJECTURE_SECTION}\nSomething substantive here.", f"{CONJECTURE_SECTION}\nTBD"
        )
        (root / "docs/features/x/brief.md").write_text(text + "\n")

    def gap_adrs(root: Path) -> None:
        (root / "docs/adr/0003-gap.md").write_text(f"# A gap\n\n{REFUTATION_SECTION}\nNone.\n")

    def bad_dispute(root: Path) -> None:
        (root / "docs/disputes/0001-bad.md").write_text("# 0001: bad\n\n- Status: maybe\n")

    def short_register_reason(root: Path) -> None:
        path = root / "quality/mutation-register.toml"
        path.write_text(
            path.read_text()
            + '\n[[survivor]]\ntool = "x"\nidentity = "y"\nreason = "short"\ndate = 2026-01-01\n'
        )

    return [
        ("ADR without a refutation criterion", adr_without_refuter),
        ("ADR with an empty refutation criterion", adr_with_an_empty_refuter),
        ("feature brief with a placeholder conjecture", brief_without_a_conjecture),
        ("ADR numbering with a gap", gap_adrs),
        ("dispute with an unknown status", bad_dispute),
        ("register entry with a short reason", short_register_reason),
    ]


UNBOUND_GROUP = re.compile(r"paths = \[\n(?:\s*# bind:[^\n]*\n)+\]")


def fill_placeholders(root: Path) -> None:
    """Stand in for adopt on a template copy: bind every group adopt would have bound."""
    path = root / "guards/manifest.toml"
    path.write_text(UNBOUND_GROUP.sub('paths = [\n    "bound/**",\n]', path.read_text()))


def self_test(templates: Path, init_templates: Path) -> int:
    def build(target: Path) -> None:
        shutil.copytree(init_templates, target, symlinks=True)
        shutil.copytree(templates, target, symlinks=True, dirs_exist_ok=True)
        fill_placeholders(target)

    with tempfile.TemporaryDirectory() as scratch:
        pristine = Path(scratch) / "pristine"
        build(pristine)
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
            build(copy)
            mutate(copy)
            bites = bool(failures(copy))
            print(f"self-test: {label}: {'bites' if bites else 'DOES NOT BITE'}")
            broken += 0 if bites else 1
    return 1 if broken else 0


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--self-test"]:
        skills = Path(__file__).resolve().parents[1] / "skills"
        return self_test(skills / "adopt/templates", skills / "init/templates")
    root = Path(argv[1]) if len(argv) > 1 else Path()
    return run(root.resolve())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
