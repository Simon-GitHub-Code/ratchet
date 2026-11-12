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

import application_types
import hooks_installed
import project_bite
import settings_rules

ENTRY_POINTS = (
    "tier0",
    "tier0-tree",
    "pre-commit",
    "commit-msg",
    "hooks-installed",
    "tier1",
    "tier2",
    "check",
    "check-fast",
    "check-deep",
    "ratchet",
    "ratchet-lowered",
    "snapshots",
    "snapshots-check",
    "suppressions",
    "commits",
    "guard-diff",
    "settings",
    "settings-check",
    "conformance",
    "merge-ready",
    "merge",
    "reports",
    "budget",
    "bite-one",
)
SPEED_SECTION = "## Speed"
# Why: ADR 0019. Speed is asked by concept from the first commit; the binding says how this
# stack meets each one, and CI reads the shard count from the `- Shards:` line.
SPEED_PARTS = ("Parallel", "One pipeline", "Shards", "Cache", "Affected only", "Ceilings")
SPEED_PART = re.compile(r"^- ([A-Z][a-z ]+):[ \t]*(.*)$", re.MULTILINE)
SHARD_COUNT = re.compile(r"[1-9]\d*")
TIER_WORKFLOWS = ("ci.yml", "deep.yml")
# Why: ADR 0021. The merge workflow runs the judge on the default branch's checkout and
# builds nothing, so it has nothing to cache; it is still pinned and serialised.
MERGE_WORKFLOW = "merge.yml"
MERGE_SHAPE = (
    ("workflow_run:", "runs it after `ci`, as the default branch holds it"),
    ("merge_ready.py", "asks the merge judge"),
)
DOOR_KINDS = ("change", "rewrite", "removal")
SHARD_MATRIX = "matrix.shard"
# Why: ADR 0012. These questions are asked of every repository from the first commit; a binding
# may answer one with `none` and a reason, but may not drop the row.
OPERABILITY_QUESTIONS = (
    "secrets",
    "feature flags declared",
    "shipped-dependency audit",
    "static security analysis",
    "configuration hardening",
    "artefact and image scan",
    "telemetry emitted",
    "changed flags off and on",
    "load smoke",
    "dynamic security scan",
    "active security scan",
    "load",
    "telemetry arrives",
    "no flag past its removal date",
)
OPERABILITY_SECTIONS = ("## Telemetry", "## Feature flags")
FLAG_REGISTRY = Path("flags.toml")
FIRST_CELL = re.compile(r"^\|\s*([^|]+?)\s*\|", re.MULTILINE)
WORKFLOWS = Path(".github/workflows")
CI_SHAPE = (("concurrency:", "cancels superseded runs"), ("cache", "caches between runs"))
ADR_STATUS = re.compile(r"^(proposed|accepted|deprecated|rejected|superseded( by ADR-\d{4})?)$")
ADR_FRONT_MATTER = re.compile(
    r"\A(?:---\nstatus: (?P<status>[^\n]+)\n(?:superseded-by: (?P<by>\d{4})\n)?---\n+)?# \S"
)
ADR_STATUS_WORDS = "proposed|accepted|deprecated|rejected|superseded"
ADR_PLACEHOLDERS = ("<X.Y.Z>", "<ratchet-sha>")
GLOSSARY_PLACEHOLDER = "<project>"
PLUGIN_CHECKOUT_PLACEHOLDER = "<ratchet-sha>"
COMMIT_HOOKS = Path(".pre-commit-config.yaml")
INSTALLS_COMMIT_MSG = re.compile(r"^default_install_hook_types:.*\bcommit-msg\b", re.MULTILINE)
COMMIT_MSG_STAGE = re.compile(r"^\s*stages:\s*\[\s*commit-msg\s*\]", re.MULTILINE)
WHOLE_TIER0_ENTRY = re.compile(r"^\s*entry:.*\btier0\b", re.MULTILINE)
REFUTATION_SECTION = "## Refutation criterion"
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
PR_SECTIONS = (
    "## Conjecture",
    "## Operability",
    "## Criticism",
    "## Guard paths",
    "## Decisions",
    "## Gates",
)
LOCKED_PATTERNS = {".claude/settings.json", ".claude/hooks/**", "guards/**"}
TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|")
# Why: a repository with no forge names a change template; adopt's own wording for it follows
# the path with a clause, so the line is read up to the closing backtick.
PR_TEMPLATE_LINE = re.compile(r"^- (?:Pull request|Change) template: `([^`]+)`", re.MULTILINE)
DEFAULT_PR_TEMPLATE = ".github/pull_request_template.md"
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
    elif GLOSSARY_PLACEHOLDER in text:
        yield f"GLOSSARY.md: still names '{GLOSSARY_PLACEHOLDER}'; name the project"


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
    if number == 1:
        for placeholder in ADR_PLACEHOLDERS:
            if placeholder in text:
                yield f"{rel}: still carries '{placeholder}'; adopt fills the plugin version"


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
    """The first two cells of every row under the header; an empty second cell is kept."""
    rows = []
    for line in body.splitlines():
        match = TABLE_ROW.match(line)
        if match and not set(match.group(1)) <= set("-: "):
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
        return
    # Why: ADR 0017. The types decide which rows apply; a row that applies is bound or an
    # accepted gap, and one that does not may say which type the repository is not.
    adrs = {number for number, _ in numbered_files(root / "docs/adr")}
    for failure in application_types.load().failures(text, table_cells(gates), adrs):
        yield f"docs/process/binding.md: {failure}"


def check_operability(root: Path) -> Iterable[str]:
    text = read(root / "docs/process/binding.md") or ""
    asked = set(FIRST_CELL.findall(section(text, "## Gates")))
    for question in OPERABILITY_QUESTIONS:
        if text and question not in asked:
            yield f"docs/process/binding.md: the Gates table has no '{question}' row"
    for heading in OPERABILITY_SECTIONS:
        if text and empty_body(text, heading):
            yield f"docs/process/binding.md: has no '{heading}' section, or it is empty"
    if not (root / FLAG_REGISTRY).is_file():
        yield f"{FLAG_REGISTRY}: missing; an empty file declares no flags"


def pr_template_path(root: Path) -> str:
    text = read(root / "docs/process/binding.md") or ""
    match = PR_TEMPLATE_LINE.search(text)
    return match.group(1) if match else DEFAULT_PR_TEMPLATE


def check_speed(root: Path) -> Iterable[str]:
    text = read(root / "docs/process/binding.md") or ""
    if text and SPEED_SECTION not in text:
        yield f"docs/process/binding.md: has no '{SPEED_SECTION}' section (ceilings, shards)"
    elif text:
        yield from check_speed_parts(section(text, SPEED_SECTION))
    workflows = root / WORKFLOWS
    if not workflows.is_dir():
        return
    for path in sorted(workflows.glob("*.yml")):
        content = path.read_text(encoding="utf-8")
        for marker, meaning in CI_SHAPE:
            builds = path.name != MERGE_WORKFLOW or marker != "cache"
            if builds and marker not in content:
                yield f"{path.relative_to(root)}: nothing {meaning} ('{marker}' absent)"
        if path.name in TIER_WORKFLOWS and SHARD_MATRIX not in content:
            yield (
                f"{path.relative_to(root)}: no long gate runs over the binding's shard count "
                f"('{SHARD_MATRIX}' absent)"
            )


def check_speed_parts(body: str) -> Iterable[str]:
    stated = dict(SPEED_PART.findall(body))
    for part in SPEED_PARTS:
        value = stated.get(part, "")
        if part == "Shards" and not SHARD_COUNT.fullmatch(value):
            yield (
                "docs/process/binding.md: the Speed section needs '- Shards: N', a whole "
                "number alone on its line, 1 on day one; CI reads it"
            )
        elif not value or value.startswith("<"):
            yield f"docs/process/binding.md: the Speed section does not say how '{part}' is met"


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


def check_doors(root: Path) -> Iterable[str]:
    """ADR 0021: every kind of one-way door is bound, or says why the repository has none."""
    text = read(root / "guards/manifest.toml")
    try:
        manifest = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return
    listed = manifest.get("door", [])
    doors = [door for door in listed if isinstance(door, dict)] if text else None
    if doors is None:
        return
    kinds = {door.get("on", "change") for door in doors}
    for kind in DOOR_KINDS:
        if kind not in kinds:
            yield (
                f"guards/manifest.toml: no [[door]] with on = '{kind}'; the merge judge reads"
                " the one-way doors from it"
            )
    for door in doors:
        name = door.get("name", "<unnamed>")
        if len(str(door.get("reason", ""))) < MIN_REASON:
            yield f"guards/manifest.toml: door '{name}' needs a reason of {MIN_REASON}+ characters"
        if not door.get("paths") and len(str(door.get("none", ""))) < MIN_REASON:
            yield (
                f"guards/manifest.toml: door '{name}' has no paths yet; bind it, or say why the"
                ' repository has none with `none = "<reason>"`'
            )


def check_merge_workflow(root: Path) -> Iterable[str]:
    """ADR 0021: a forge that runs ``ci`` has the workflow that merges what the judge passes."""
    if not (root / WORKFLOWS / "ci.yml").is_file():
        return
    text = read(root / WORKFLOWS / MERGE_WORKFLOW)
    if text is None:
        yield f"{WORKFLOWS / MERGE_WORKFLOW}: missing; nothing merges a change the judge passes"
        return
    for marker, meaning in MERGE_SHAPE:
        if marker not in text:
            yield f"{WORKFLOWS / MERGE_WORKFLOW}: nothing {meaning} ('{marker}' absent)"


def check_plugin_checkout(root: Path) -> Iterable[str]:
    for name in (*TIER_WORKFLOWS, MERGE_WORKFLOW):
        text = read(root / WORKFLOWS / name)
        if text is not None and PLUGIN_CHECKOUT_PLACEHOLDER in text:
            yield (
                f"{WORKFLOWS / name}: checks the plugin out at '{PLUGIN_CHECKOUT_PLACEHOLDER}'; "
                "adopt fills the commit ADR 0001 names"
            )


def check_commit_hooks(root: Path) -> Iterable[str]:
    text = read(root / COMMIT_HOOKS)
    if text is None:
        yield f"{COMMIT_HOOKS}: missing; the tier 0 commit hooks are installed from it"
        return
    if not (INSTALLS_COMMIT_MSG.search(text) and COMMIT_MSG_STAGE.search(text)):
        yield f"{COMMIT_HOOKS}: no commit-msg hook installed, so a bad subject is first seen in CI"
    if WHOLE_TIER0_ENTRY.search(text):
        yield (
            f"{COMMIT_HOOKS}: a hook runs the whole tier0, whose history gate judges the commit "
            "an amend replaces; run `pre-commit` and `commit-msg` instead"
        )
    justfile = read(root / "justfile")
    if justfile is not None and hooks_installed.PROBE_ENV not in justfile:
        yield (
            f"justfile: the `pre-commit` and `commit-msg` recipes do not answer "
            f"{hooks_installed.PROBE_ENV}, so `hooks_installed.py` cannot tell whether a commit "
            f"would run tier 0; copy them from {hooks_installed.TEMPLATE}"
        )


def check_settings(root: Path) -> Iterable[str]:
    if (root / settings_rules.MANIFEST).is_file():
        yield from settings_rules.drift(root)


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


def check_bites(root: Path) -> Iterable[str]:
    # Why: ADR 0020. The method ships no tool, so the repository shows each gate it bound going
    # red on a planted fault; a gate with no bite, or a collection never shown empty, is unproven.
    return project_bite.failures(root)


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
    # Why: the pipeline's spec role writes docs/features/<slug>/spec.md; the directory is the
    # method's. The feature's input is a tracker ticket, which the feature preflight checks.
    if not (root / "docs/features").is_dir():
        yield "docs/features: missing"


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
    check_operability,
    check_speed,
    check_manifest,
    check_doors,
    check_merge_workflow,
    check_settings,
    check_commit_hooks,
    check_plugin_checkout,
    check_quality,
    check_bites,
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
    return [
        *artefact_mutations(),
        *type_mutations(),
        *bite_mutations(),
        *speed_mutations(),
        *operability_mutations(),
        *hook_mutations(),
        *guard_mutations(),
        *merge_mutations(),
        *record_mutations(),
    ]


def artefact_mutations() -> list[Mutation]:
    def strip_stanza(root: Path) -> None:
        path = root / "AGENTS.md"
        path.write_text(path.read_text().replace("ratchet:method", "nothing"))

    def drop_entry_point(root: Path) -> None:
        path = root / "docs/process/binding.md"
        path.write_text(path.read_text().replace("| conformance |", "| conform |"))

    def drop_pr_section(root: Path) -> None:
        path = root / DEFAULT_PR_TEMPLATE
        path.write_text(path.read_text().replace("## Guard paths", "## Guards"))

    def ci_with_the_plugin_at_a_placeholder(root: Path) -> None:
        path = root / WORKFLOWS / "ci.yml"
        path.write_text(path.read_text().replace("0" * 40, PLUGIN_CHECKOUT_PLACEHOLDER, 1))

    def glossary_naming_the_placeholder(root: Path) -> None:
        path = root / "GLOSSARY.md"
        path.write_text(path.read_text().replace("the project", GLOSSARY_PLACEHOLDER, 1))

    return [
        ("AGENTS.md without the method stanza", strip_stanza),
        ("binding without the conformance entry point", drop_entry_point),
        ("pull-request template missing a section", drop_pr_section),
        (
            "CI workflow checking the plugin out at a placeholder",
            ci_with_the_plugin_at_a_placeholder,
        ),
        ("glossary still naming <project>", glossary_naming_the_placeholder),
    ]


def rebind(old: str, new: str) -> Callable[[Path], None]:
    """A mutation that rewrites one piece of the binding."""

    def mutate(root: Path) -> None:
        path = root / "docs/process/binding.md"
        text = path.read_text()
        if old not in text:
            message = f"the self-test's binding has no {old!r} to mutate"
            raise SystemExit(message)
        path.write_text(text.replace(old, new))

    return mutate


def type_mutations() -> list[Mutation]:
    """The filled binding is a `cli`: `contract drift` is the one row its type leaves out."""
    types = "- Application types: cli"
    return [
        ("binding without an Application types line", rebind(types, "- Kind: cli")),
        ("binding naming an unknown application type", rebind(types, f"{types}, website")),
        ("binding with an empty Tool cell", rebind("| format | a tool |", "| format | |")),
        (
            "binding with a gate bound to none and no reason",
            rebind("| format | a tool |", "| format | none |"),
        ),
        (
            "binding with a gate that applies bound to none under no ADR",
            rebind("| format | a tool |", "| format | none: no formatter exists for it |"),
        ),
        (
            "binding with a gate bound to none under an ADR the repository lacks",
            rebind("| format | a tool |", "| format | none: no formatter; ADR 0042 |"),
        ),
        (
            "binding saying 'not a service' of a row a service is asked",
            rebind(types, "- Application types: service"),
        ),
        (
            "binding saying 'not a' type the row is not asked of",
            rebind("none: not a service |", "none: not a library |"),
        ),
        (
            "binding that drops the row its type leaves out",
            rebind("| contract drift | none: not a service | 1 | | `just contracts-check` |\n", ""),
        ),
    ]


def reregister(old: str, new: str) -> Callable[[Path], None]:
    """A mutation that rewrites one piece of the bite registry."""

    def mutate(root: Path) -> None:
        path = root / project_bite.REGISTRY
        text = path.read_text()
        if old not in text:
            message = f"the self-test's registry has no {old!r} to mutate"
            raise SystemExit(message)
        path.write_text(text.replace(old, new, 1))

    return mutate


def bite_mutations() -> list[Mutation]:
    """The filled registry plants one fault per bound gate, and empties each collection."""

    def no_registry(root: Path) -> None:
        (root / project_bite.REGISTRY).unlink()

    lint = 'gate = "lint"\nname = "a fault"\n'
    empty = 'gate = "perf"\nname = "nothing to run"\nremove = ["collection/**"]\n'
    slow = 'gate = "perf"\nname = "slow"\nwrite = { path = "a", content = "b" }\n'
    return [
        ("repository without the bite registry", no_registry),
        ("bound gate with no bite", reregister(lint, 'gate = "fmt-check"\nname = "another"\n')),
        ("collection gate never shown red when empty", reregister(empty, slow)),
        ("bite that does not say why the gate is red", reregister('expect = "a fault"\n', "")),
        ("bite naming a gate the binding lacks", reregister(lint, 'gate = "lnit"\nname = "x"\n')),
        (
            "bite planting two faults",
            reregister(empty, empty + 'write = { path = "a", content = "b" }\n'),
        ),
    ]


def speed_mutations() -> list[Mutation]:
    def ci_without_concurrency(root: Path) -> None:
        path = root / WORKFLOWS / "ci.yml"
        path.write_text(path.read_text().replace("concurrency:", "serial:"))

    def ci_without_cache(root: Path) -> None:
        path = root / WORKFLOWS / "deep.yml"
        path.write_text(path.read_text().replace("cache", "store"))

    def ci_without_a_shard_matrix(root: Path) -> None:
        path = root / WORKFLOWS / "ci.yml"
        path.write_text(path.read_text().replace(SHARD_MATRIX, "matrix.part"))

    stated = "stated for this stack"
    return [
        ("binding without a Speed section", rebind(SPEED_SECTION, "## Slowness")),
        ("Speed section without a part", rebind("- Cache: ", "- Store: ")),
        (
            "Speed section with a part left unfilled",
            rebind(f"- Parallel: {stated}", "- Parallel: <how>"),
        ),
        ("Speed section with no shard count", rebind("- Shards: 1", "- Shards: a few")),
        ("CI workflow without concurrency control", ci_without_concurrency),
        ("CI workflow without a cache", ci_without_cache),
        ("CI workflow without a shard matrix", ci_without_a_shard_matrix),
    ]


def operability_mutations() -> list[Mutation]:
    def drop_row(root: Path) -> None:
        path = root / "docs/process/binding.md"
        path.write_text(path.read_text().replace("| telemetry emitted |", "| tracing |"))

    def drop_section(root: Path) -> None:
        path = root / "docs/process/binding.md"
        path.write_text(path.read_text().replace("## Feature flags", "## Toggles"))

    def drop_flag_registry(root: Path) -> None:
        (root / FLAG_REGISTRY).unlink()

    return [
        ("binding without the telemetry row", drop_row),
        ("binding without a Feature flags section", drop_section),
        ("repository without the flag registry", drop_flag_registry),
    ]


def hook_mutations() -> list[Mutation]:
    def hooks_without_commit_msg(root: Path) -> None:
        path = root / COMMIT_HOOKS
        path.write_text(path.read_text().replace("stages: [commit-msg]", "stages: [pre-push]"))

    def hooks_running_the_whole_tier(root: Path) -> None:
        path = root / COMMIT_HOOKS
        path.write_text(path.read_text().replace("entry: just pre-commit", "entry: just tier0"))

    def recipes_that_cannot_answer_the_probe(root: Path) -> None:
        path = root / "justfile"
        path.write_text(path.read_text().replace(hooks_installed.PROBE_ENV, "NO_PROBE"))

    return [
        ("commit hooks without the commit-msg stage", hooks_without_commit_msg),
        ("commit hooks running the whole tier0", hooks_running_the_whole_tier),
        ("hook recipes that cannot answer the probe", recipes_that_cannot_answer_the_probe),
    ]


def guard_mutations() -> list[Mutation]:
    def unlock_harness(root: Path) -> None:
        path = root / "guards/manifest.toml"
        path.write_text(path.read_text().replace('edit = "deny"', 'edit = "ask"'))

    def unbound_group(root: Path) -> None:
        path = root / "guards/manifest.toml"
        path.write_text(path.read_text().replace('paths = [\n    "quality/**",\n]', "paths = []"))

    def settings_with_a_foreign_deny(root: Path) -> None:
        path = root / settings_rules.SETTINGS
        path.write_text(
            path.read_text().replace(
                '"Bash(gh label edit:*)",',
                '"Bash(gh label edit:*)",\n      "Bash(gh label create:*)",',
            )
        )

    def settings_missing_a_rule(root: Path) -> None:
        path = root / settings_rules.SETTINGS
        path.write_text(path.read_text().replace('      "Edit(/GLOSSARY-MAP.md)",\n', ""))

    return [
        ("manifest with the harness unlocked", unlock_harness),
        ("manifest with a group still unbound", unbound_group),
        ("settings denying what the hook allows", settings_with_a_foreign_deny),
        ("settings missing a manifest rule", settings_missing_a_rule),
    ]


def rewrite(name: str, old: str, new: str) -> Callable[[Path], None]:
    """A mutation that rewrites one piece of a template file, and refuses when it is gone."""

    def mutate(root: Path) -> None:
        path = root / name
        text = path.read_text()
        if old not in text:
            message = f"the self-test's {name} has no {old!r} to mutate"
            raise SystemExit(message)
        path.write_text(text.replace(old, new))

    return mutate


def merge_mutations() -> list[Mutation]:
    def no_merge_workflow(root: Path) -> None:
        (root / WORKFLOWS / MERGE_WORKFLOW).unlink()

    manifest, workflow = "guards/manifest.toml", str(WORKFLOWS / MERGE_WORKFLOW)
    bound = 'paths = [\n    "bound/**",\n]'
    return [
        (
            "manifest without a kind of one-way door",
            rewrite(manifest, 'on = "rewrite"', 'on = "change"'),
        ),
        (
            "manifest with a one-way door still unbound",
            rewrite(manifest, f'on = "removal"\n{bound}', 'on = "removal"\npaths = []'),
        ),
        ("forge without the merge workflow", no_merge_workflow),
        (
            "merge workflow that does not ask the judge",
            rewrite(workflow, "merge_ready.py", "merged.py"),
        ),
        (
            "merge workflow that a pull request could change",
            rewrite(workflow, "workflow_run:", "pull_request:"),
        ),
        (
            "binding without the merge entry point",
            rewrite("docs/process/binding.md", "| merge | `just merge` |\n", ""),
        ),
    ]


def record_mutations() -> list[Mutation]:
    def adr_without_refuter(root: Path) -> None:
        (root / "docs/adr/0002-plain.md").write_text("# We keep it plain\n\nBecause.\n")

    def adr_0001_with_the_version_placeholder(root: Path) -> None:
        path = root / "docs/adr/0001-adopt-the-ratchet-method.md"
        path.write_text(path.read_text().replace("0.0.0", "<X.Y.Z>", 1))

    def adr_with_an_empty_refuter(root: Path) -> None:
        (root / "docs/adr/0002-empty.md").write_text(
            f"# We keep it empty\n\nBecause.\n\n{REFUTATION_SECTION}\nNone.\n"
        )

    def features_directory_removed(root: Path) -> None:
        (root / "docs/features/.gitkeep").unlink()
        (root / "docs/features").rmdir()

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
        ("ADR 0001 with the version placeholder", adr_0001_with_the_version_placeholder),
        ("features directory removed", features_directory_removed),
        ("ADR numbering with a gap", gap_adrs),
        ("dispute with an unknown status", bad_dispute),
        ("register entry with a short reason", short_register_reason),
    ]


UNBOUND_GROUP = re.compile(r"paths = \[\n(?:\s*# bind:[^\n]*\n)+\]")
TYPES_PLACEHOLDER = re.compile(r"^- Application types: <[^\n]*>$", re.MULTILINE)
EMPTY_TOOL_CELL = re.compile(r"^(\| [^|\n]+ \|) \|", re.MULTILINE)
SPEED_PLACEHOLDER = re.compile(r"^(- [A-Z][a-z ]+: )<[^\n]*>$", re.MULTILINE)


def fill_binding(text: str) -> str:
    """A command line tool's binding: one row its type leaves out, a tool in every other."""
    text = TYPES_PLACEHOLDER.sub("- Application types: cli", text)
    text = text.replace("| contract drift | |", "| contract drift | none: not a service |")
    text = EMPTY_TOOL_CELL.sub(r"\1 a tool |", text)
    return SPEED_PLACEHOLDER.sub(r"\1stated for this stack", text)


def fill_registry(binding: str) -> str:
    """A fault per gate the filled binding binds to a tool, and each collection gate's emptied."""
    collections = application_types.load().collections
    entries = []
    for row in project_bite.gate_rows(binding):
        if not row.bound:
            continue
        head = f'[[bite]]\ngate = "{row.gate}"\n'
        fault = 'write = { path = "src/bite", content = "a fault" }\nexpect = "a fault"\n'
        entries.append(f'{head}name = "a fault"\n{fault}')
        if row.question in collections:
            emptied = 'remove = ["collection/**"]\nexpect = "nothing to run"\n'
            entries.append(f'{head}name = "nothing to run"\n{emptied}')
    return "\n".join(entries)


def fill_placeholders(root: Path) -> None:
    """Stand in for adopt on a template copy: bind every group and row adopt would have bound."""
    path = root / "guards/manifest.toml"
    path.write_text(UNBOUND_GROUP.sub('paths = [\n    "bound/**",\n]', path.read_text()))
    settings_rules.write(root)
    binding = root / "docs/process/binding.md"
    binding.write_text(fill_binding(binding.read_text()))
    registry = root / project_bite.REGISTRY
    registry.write_text(registry.read_text() + fill_registry(binding.read_text()))
    fills = {
        "docs/adr/0001-adopt-the-ratchet-method.md": {
            "<X.Y.Z>": "0.0.0",
            "<ratchet-sha>": "0" * 40,
        },
        "GLOSSARY.md": {GLOSSARY_PLACEHOLDER: "the project"},
        str(WORKFLOWS / "ci.yml"): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
        str(WORKFLOWS / "deep.yml"): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
        str(WORKFLOWS / MERGE_WORKFLOW): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
    }
    for name, replacements in fills.items():
        target = root / name
        text = target.read_text()
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        target.write_text(text)


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
