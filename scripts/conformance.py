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
from dataclasses import dataclass
from pathlib import Path

import application_types
import hooks_installed
import plugin_pin
import project_bite
import settings_rules
import triggers

ENTRY_POINTS = (
    "tier0",
    "tier0-tree",
    "pre-commit",
    "commit-msg",
    "hooks-installed",
    "hooks-install",
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
    "weekly",
    "reports",
    "budget",
    "bite-one",
    "triggers",
)
SPEED_SECTION = "## Speed"
# Why: ADR 0019. Speed is asked by concept from the first commit; the binding says how this
# stack meets each one, and CI reads the shard count from the `- Shards:` line.
SPEED_PARTS = ("Parallel", "One pipeline", "Shards", "Cache", "Affected only", "Ceilings")
SPEED_PART = re.compile(r"^- ([A-Z][a-z ]+):[ \t]*(.*)$", re.MULTILINE)
SHARD_COUNT = re.compile(r"[1-9]\d*")
TIER_WORKFLOWS = ("ci.yml", "deep.yml")
# Why: the `shape` job of each tier workflow reads these from the `justfile` with
# `just --evaluate`, and a justfile from before the tier lists has none of them.
TIER_LISTS = {
    "ci.yml": (
        ("tier1_gates", "every gate your `tier1` recipe runs"),
        ("tier1_sharded", 'the gates of tier 1 whose tool can run one `SHARD` of `SHARDS`, or ""'),
    ),
    "deep.yml": (
        ("tier2_gates", "every gate your `tier2` recipe runs"),
        ("tier2_sharded", 'the gates of tier 2 whose tool can run one `SHARD` of `SHARDS`, or ""'),
    ),
}
# Why: ADR 0021. The merge workflow runs the judge on the default branch's checkout and
# builds nothing, so it has nothing to cache; it is still pinned and serialised.
MERGE_WORKFLOW = "merge.yml"
MERGE_SHAPE = (
    ("workflow_run:", "runs it after `ci`, as the default branch holds it"),
    ("merge_ready.py", "asks the merge judge"),
    ("forge_runs.py", "says which `ci` and `deep` runs count (ADR 0024)"),
)
WEEKLY_WORKFLOW = "weekly.yml"
WEEKLY_SHAPE = (
    ("schedule:", "runs the weekly gates on a schedule"),
    ("issues: write", "may open the issue a finding gets"),
)
DOOR_KINDS = ("change", "rewrite", "removal")
SHARD_MATRIX = "matrix.shard"
HELD_TO_TIER = "holds a gate's job to its tier's ceiling"
CEILING_SHAPE = (
    ("ci.yml", "--over", "says that a ceiling reports on a pull request and blocks elsewhere"),
    ("ci.yml", "--tier", HELD_TO_TIER),
    ("deep.yml", "--tier", HELD_TO_TIER),
)
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
# Why: ADR 0028. A section describes how its rows are met, so it is asked for while any of them is
# bound and not before: a repository whose rows all say `later:` has nothing yet to describe.
OPERABILITY_SECTIONS = {
    "## Telemetry": ("telemetry emitted", "telemetry arrives"),
    "## Feature flags": ("changed flags off and on", "no flag past its removal date"),
}
INCIDENTS = Path("docs/incidents")
INCIDENT_BITE = re.compile(r"^[-*\s]*bite:\s*`?(.+?)`?\s*$", re.IGNORECASE | re.MULTILINE)
TRIGGERS_PATH = "docs/process/binding.md"
FLAG_REGISTRY = Path("flags.toml")
FIRST_CELL = re.compile(r"^\|\s*([^|]+?)\s*\|", re.MULTILINE)
WORKFLOWS = Path(".github/workflows")
CI_SHAPE = (("concurrency:", "cancels superseded runs"), ("cache", "caches between runs"))
ADR_STATUS = re.compile(r"^(proposed|accepted|deprecated|rejected|superseded( by ADR-\d{4})?)$")
ADR_FRONT_MATTER = re.compile(
    r"\A(?:---\nstatus: (?P<status>[^\n]+)\n(?:superseded-by: (?P<by>\d{4})\n)?---\n+)?# \S"
)
ADR_STATUS_WORDS = "proposed|accepted|deprecated|rejected|superseded"
GLOSSARY_PLACEHOLDER = "<project>"
PLUGIN_PIN_PLACEHOLDER = "<X.Y.Z> at <ratchet-sha>"
PLUGIN_CHECKOUT_PLACEHOLDER = "<ratchet-sha>"
PLUGIN_REF = re.compile(r'repository:\s*\S*ratchet\S*?[,\s]+ref:\s*"?([^",\s}]+)')
COMMIT_HOOKS = Path(hooks_installed.COMMIT_HOOKS)
RUNS_WHOLE_TIER0 = re.compile(r"^(?!\s*#).*\btier0\b(?!-)", re.MULTILINE)
WHOLE_TIER0 = (
    "a hook runs the whole tier0, whose history gate judges the commit an amend replaces; "
    "run `pre-commit` and `commit-msg` instead"
)
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
TEMPLATES = Path(__file__).resolve().parents[1] / "skills"
ADOPT_TEMPLATES = TEMPLATES / "adopt/templates"
AGENTS_TEMPLATE = TEMPLATES / "init/templates/AGENTS.md"
ASKING = "## Asking the owner"
RUNS_JUST = re.compile(r"^just\s+@?([A-Za-z_][\w-]*)")
INCLUDES = re.compile(r"^(?:import|mod)\b", re.MULTILINE)
# Why: a gate over the change compares with a base; `changed.py` hands it one, and judges nothing
# on the adoption, whose base holds no binding.
OVER_THE_CHANGE = frozenset({"diff-coverage", "test-counts", "mutation-changed"})
# Why: these gates judge elapsed time, which a gate running beside them disturbs.
TIMED = frozenset({"perf", "load"})


@dataclass(frozen=True)
class Auto:
    """A fix the upgrade makes itself: one line for the report, and the text it makes of a file."""

    does: str
    want: Callable[[str | None], str]


@dataclass(frozen=True)
class Agent:
    """A fix that needs judgement: the template text to start from, and what an answer asks next."""

    start: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


@dataclass(frozen=True)
class Owner:
    """A call only the owner makes: the question, what the repository shows of it, what follows."""

    ask: str
    evidence: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


Fix = Auto | Agent | Owner


@dataclass(frozen=True)
class Finding:
    """One thing the repository lacks: the file, what is wrong, and who fixes it.

    The conformance gate prints the first two on one line; ``upgrade.py`` prints the fix too.
    """

    path: str
    problem: str
    fix: Fix

    def __str__(self) -> str:
        """The one line the conformance gate prints."""
        return f"{self.path}: {self.problem}"


Check = Callable[[Path], Iterable[Finding]]


def agent(line: str, start: tuple[str, ...] = (), then: tuple[str, ...] = ()) -> Finding:
    """A finding an agent fixes, from a check's one line: every line starts with its path."""
    path, _, problem = line.partition(": ")
    return Finding(path, problem, Agent(start, then))


def read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def code_of(justfile: str) -> str:
    """A justfile less its whole-line comments, where the template names what it explains."""
    return "\n".join(line for line in justfile.splitlines() if not line.lstrip().startswith("#"))


def definition(text: str, name: str) -> tuple[str, ...]:
    """The recipe or variable ``name`` of a justfile: its comment, its head and its body."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        head = re.match(r"@?([A-Za-z_][\w-]*)", line)
        if head is None or head.group(1) != name:
            continue
        start = index
        while start > 0 and lines[start - 1].startswith("#"):
            start -= 1
        end = index + 1
        while end < len(lines) and lines[end].startswith((" ", "\t")):
            end += 1
        return tuple(lines[start:end])
    return ()


def template_definition(name: str) -> tuple[str, ...]:
    return definition(read(ADOPT_TEMPLATES / "justfile") or "", name)


def defines_recipe(justfile: str, name: str) -> bool:
    pattern = rf"^@?{re.escape(name)}(?:[ \t][^\n]*)?:(?!=)"
    return re.search(pattern, justfile, re.MULTILINE) is not None


def check_agent_instructions(root: Path) -> Iterable[Finding]:
    name = "AGENTS.md"
    text = read(root / name)
    if text is None:
        name, text = "CLAUDE.md", read(root / "CLAUDE.md")
    if text is None:
        yield agent("AGENTS.md (or CLAUDE.md): missing")
    elif "ratchet:method" not in text:
        yield agent("AGENTS.md: does not tell agents to load the ratchet:method skill")
    elif ASKING not in text:
        yield Finding(
            name,
            f"has no '{ASKING}' section; copy it from the template AGENTS.md",
            Agent(start=(ASKING, "", section(read(AGENTS_TEMPLATE) or "", ASKING).strip())),
        )


def check_glossary(root: Path) -> Iterable[Finding]:
    if (root / "GLOSSARY-MAP.md").is_file():
        return
    text = read(root / "GLOSSARY.md")
    if text is None:
        yield agent("GLOSSARY.md (or GLOSSARY-MAP.md): missing")
    elif "## Language" not in text:
        yield agent("GLOSSARY.md: has no '## Language' section")
    elif GLOSSARY_PLACEHOLDER in text:
        yield agent(f"GLOSSARY.md: still names '{GLOSSARY_PLACEHOLDER}'; name the project")


def check_brief(root: Path) -> Iterable[Finding]:
    text = read(root / "docs/design-brief.md")
    if text is None:
        yield agent("docs/design-brief.md: missing")
        return
    for heading in ("## Slice one", "## Explicitly out"):
        if heading not in text:
            yield agent(f"docs/design-brief.md: has no '{heading}' section")


def numbered_files(directory: Path) -> list[tuple[int, Path]]:
    found = []
    for path in sorted(directory.glob("*.md")):
        match = NUMBERED.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return found


def check_adrs(root: Path) -> Iterable[Finding]:
    directory = root / "docs/adr"
    if not directory.is_dir():
        yield agent("docs/adr: missing")
        return
    files = numbered_files(directory)
    if not files:
        yield agent("docs/adr: no NNNN-<slug>.md record")
        return
    numbers = [number for number, _ in files]
    if numbers != list(range(1, len(numbers) + 1)):
        yield agent(f"docs/adr: numbering has gaps or duplicates: {numbers}")
    for number, path in files:
        yield from check_adr(number, path, path.relative_to(root))


def check_adr(number: int, path: Path, rel: Path) -> Iterable[Finding]:
    text = path.read_text(encoding="utf-8")
    match = ADR_FRONT_MATTER.match(text)
    if match is None:
        yield agent(f"{rel}: opens with a '# ' title, optionally after 'status:' front matter")
        return
    status, superseded_by = match.group("status"), match.group("by")
    if status is not None and not ADR_STATUS.match(status):
        yield agent(f"{rel}: status '{status}' is not {ADR_STATUS_WORDS}")
    if status == "superseded" and superseded_by is None:
        yield agent(f"{rel}: a superseded ADR names its successor: 'superseded-by: NNNN'")
    if REFUTATION_SECTION not in text:
        yield agent(f"{rel}: has no '{REFUTATION_SECTION}' section")
    elif empty_body(text, REFUTATION_SECTION):
        yield agent(f"{rel}: '{REFUTATION_SECTION}' names no observation that would show it wrong")
    if number == 1 and "Ratchet method" not in text:
        yield agent(f"{rel}: ADR 0001 records adopting the Ratchet method")


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


def template_rows() -> dict[str, tuple[str, str]]:
    """The template binding's Entry points rows by name: the row as written, and its command."""
    body = section(read(ADOPT_TEMPLATES / "docs/process/binding.md") or "", "## Entry points")
    return {
        match.group(1): (line, match.group(2))
        for line in body.splitlines()
        if (match := TABLE_ROW.match(line))
    }


def template_gate_row(question: str) -> str:
    """The template binding's Gates row for a question (a test holds that it has one)."""
    body = section(read(ADOPT_TEMPLATES / "docs/process/binding.md") or "", "## Gates")
    prefix = f"| {question} |"
    return next(line for line in body.splitlines() if line.startswith(prefix))


def template_block(heading: str) -> tuple[str, ...]:
    """A section of the template binding, heading included, for the upgrade report to copy."""
    text = read(ADOPT_TEMPLATES / "docs/process/binding.md") or ""
    return (heading, *section(text, heading).rstrip().splitlines())


def check_entry_points(root: Path, text: str) -> Iterable[Finding]:
    """Every entry point has a row naming its command, and a ``just`` command has its recipe."""
    commands = dict(table_cells(section(text, "## Entry points")))
    rows = template_rows()
    justfile = read(root / "justfile")
    for name in ENTRY_POINTS:
        command = commands.get(name)
        if command is None:
            row, command = rows[name]
            yield Finding(
                "docs/process/binding.md",
                f"the Entry points table has no row for '{name}'; add the template's row {row},"
                " with your runner's command",
                Agent(start=(row,)),
            )
        elif not command or "<" in command:
            yield agent(f"docs/process/binding.md: entry point '{name}' names no command")
            continue
        recipe = RUNS_JUST.match(command.strip("`"))
        if justfile is None or INCLUDES.search(justfile) or recipe is None:
            continue
        if not defines_recipe(justfile, recipe.group(1)):
            yield Finding(
                "justfile",
                f"has no `{recipe.group(1)}` recipe, which the binding's entry point '{name}'"
                " runs; copy it from the template justfile",
                Agent(start=template_definition(recipe.group(1))),
            )


def check_binding(root: Path) -> Iterable[Finding]:
    text = read(root / "docs/process/binding.md")
    if text is None:
        yield agent("docs/process/binding.md: missing")
        return
    yield from check_entry_points(root, text)
    gates = section(text, "## Gates")
    if not gates:
        yield agent("docs/process/binding.md: has no '## Gates' section")
        return
    # Why: ADR 0017. The types decide which rows apply; a row that applies is bound or an
    # accepted gap, and one that does not may say which type the repository is not.
    adrs = {number for number, _ in numbered_files(root / "docs/adr")}
    catalogue = application_types.load()
    rows = table_cells(gates)
    # Why: a row the catalogue gained since the adoption is an upgrade item with its template row.
    dropped = {
        application_types.dropped(question): question
        for question in sorted(catalogue.questions - {question for question, _ in rows})
    }
    for failure in catalogue.failures(text, rows, adrs):
        question = dropped.get(failure)
        if question is None:
            yield agent(f"docs/process/binding.md: {failure}")
            continue
        yield Finding(
            "docs/process/binding.md",
            f"{failure}; add the template's row, bound or `later: <trigger>`",
            Agent(start=(template_gate_row(question),)),
        )


def declared_events(text: str) -> Iterable[Finding]:
    """The ``- Life events:`` line (ADR 0028): present, and naming only events the catalogue has."""
    match = application_types.EVENTS.search(text)
    if match is None:
        yield Finding(
            TRIGGERS_PATH,
            "has no '- Life events:' line; add it beside '- Application types:'. Without it every"
            " event counts as declared; `none` turns off the owner's review of one-way doors and"
            " unflagged surface and the critic, so a project that is live writes its events"
            " instead (ADR 0028)",
            Agent(start=("- Life events: none",)),
        )
        return
    known = application_types.load().events
    for name in sorted(application_types.life_events(text)):
        if name not in known:
            yield agent(
                f"{TRIGGERS_PATH}: '- Life events:' names '{name}'; the events are"
                f" {', '.join(known)}"
            )


def check_triggers(root: Path) -> Iterable[Finding]:
    """The Life events line and the Triggers table: how a repository says a trigger has fired.

    Why: ADR 0028. The ``triggers`` gate counts instances with this table, so every kind a question
    asked of the repository's types names must have a row, or the trigger could never fire.
    """
    text = read(root / TRIGGERS_PATH)
    if text is None:
        return
    yield from declared_events(text)
    catalogue = application_types.load()
    if triggers.SECTION.strip() not in text:
        yield Finding(
            TRIGGERS_PATH,
            "has no '## Triggers' section; add the template's, one row per kind of code a question"
            " asked of these types names (ADR 0028)",
            Agent(start=template_block("## Triggers")),
        )
        return
    kinds, problems = triggers.parse(text)
    for problem in problems:
        yield agent(f"{TRIGGERS_PATH}: {problem}")
    for name in kinds:
        if name not in catalogue.kinds:
            yield agent(
                f"{TRIGGERS_PATH}: the Triggers table names kind '{name}', which the catalogue"
                f" lacks; the kinds are {', '.join(catalogue.kinds)}"
            )
    types = set(application_types.declared(text) or [])
    for name in sorted(catalogue.kinds_asked(types) - set(kinds)):
        yield agent(
            f"{TRIGGERS_PATH}: the Triggers table has no row for kind '{name}', which a question"
            f" asked of these types is triggered by ({catalogue.kinds[name]});"
            " write its Paths, or `none: <reason>`"
        )


def later(tool: str) -> bool:
    """Whether a Tool cell says its trigger has not fired (ADR 0028)."""
    return application_types.LATER.match(tool) is not None


def check_operability(root: Path) -> Iterable[Finding]:
    text = read(root / "docs/process/binding.md") or ""
    asked = set(FIRST_CELL.findall(section(text, "## Gates")))
    for question in OPERABILITY_QUESTIONS:
        if text and question not in asked:
            yield agent(f"docs/process/binding.md: the Gates table has no '{question}' row")
    waiting = {row.question for row in project_bite.gate_rows(text) if later(row.tool)}
    for heading, questions in OPERABILITY_SECTIONS.items():
        if text and not waiting >= set(questions) and empty_body(text, heading):
            yield agent(f"docs/process/binding.md: has no '{heading}' section, or it is empty")
    if not (root / FLAG_REGISTRY).is_file():
        yield agent(f"{FLAG_REGISTRY}: missing; an empty file declares no flags")


def pr_template_path(root: Path) -> str:
    text = read(root / "docs/process/binding.md") or ""
    match = PR_TEMPLATE_LINE.search(text)
    return match.group(1) if match else DEFAULT_PR_TEMPLATE


def check_speed(root: Path) -> Iterable[Finding]:
    text = read(root / "docs/process/binding.md") or ""
    if text and SPEED_SECTION not in text:
        yield agent(f"docs/process/binding.md: has no '{SPEED_SECTION}' section (ceilings, shards)")
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
                yield agent(f"{path.relative_to(root)}: nothing {meaning} ('{marker}' absent)")
        for name, marker, meaning in CEILING_SHAPE:
            if path.name == name and marker not in content:
                yield agent(f"{path.relative_to(root)}: nothing {meaning} ('{marker}' absent)")
        if path.name in TIER_WORKFLOWS and SHARD_MATRIX not in content:
            yield agent(
                f"{path.relative_to(root)}: no long gate runs over the binding's shard count "
                f"('{SHARD_MATRIX}' absent)"
            )


def check_speed_parts(body: str) -> Iterable[Finding]:
    stated = dict(SPEED_PART.findall(body))
    for part in SPEED_PARTS:
        value = stated.get(part, "")
        if part == "Shards" and not SHARD_COUNT.fullmatch(value):
            yield agent(
                "docs/process/binding.md: the Speed section needs '- Shards: N', a whole "
                "number alone on its line, 1 on day one; CI reads it"
            )
        elif not value or value.startswith("<"):
            yield agent(
                f"docs/process/binding.md: the Speed section does not say how '{part}' is met"
            )


def check_manifest(root: Path) -> Iterable[Finding]:
    path = root / "guards/manifest.toml"
    if not path.is_file():
        yield agent("guards/manifest.toml: missing")
        return
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        yield agent(f"guards/manifest.toml: {error}")
        return
    groups = manifest.get("group")
    if not isinstance(groups, list) or not groups:
        yield agent("guards/manifest.toml: has no [[group]]")
        return
    locked: set[str] = set()
    for group in groups:
        name = group.get("name", "<unnamed>")
        if len(str(group.get("reason", ""))) < MIN_REASON:
            yield agent(
                f"guards/manifest.toml: group '{name}' needs a reason of {MIN_REASON}+ characters"
            )
        if not isinstance(group.get("paths"), list):
            yield agent(f"guards/manifest.toml: group '{name}' needs a paths list")
        elif not group["paths"]:
            yield agent(f"guards/manifest.toml: group '{name}' has no paths yet; bind or remove it")
        elif group.get("edit") == "deny":
            locked.update(group["paths"])
    for pattern in sorted(LOCKED_PATTERNS - locked):
        yield agent(f"guards/manifest.toml: '{pattern}' is not in an edit = \"deny\" group")


def check_doors(root: Path) -> Iterable[Finding]:
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
            yield agent(
                f"guards/manifest.toml: no [[door]] with on = '{kind}'; the merge judge reads"
                " the one-way doors from it"
            )
    for door in doors:
        name = door.get("name", "<unnamed>")
        if len(str(door.get("reason", ""))) < MIN_REASON:
            yield agent(
                f"guards/manifest.toml: door '{name}' needs a reason of {MIN_REASON}+ characters"
            )
        if not door.get("paths") and len(str(door.get("none", ""))) < MIN_REASON:
            yield agent(
                f"guards/manifest.toml: door '{name}' has no paths yet; bind it, or say why the"
                ' repository has none with `none = "<reason>"`'
            )


def forge_workflow(
    root: Path, name: str, *, unmet: str, shape: tuple[tuple[str, str], ...]
) -> Iterable[Finding]:
    """A workflow that a forge running ``ci`` must also have, and the words that show its shape."""
    if not (root / WORKFLOWS / "ci.yml").is_file():
        return
    text = read(root / WORKFLOWS / name)
    if text is None:
        yield agent(f"{WORKFLOWS / name}: missing; {unmet}")
        return
    for marker, meaning in shape:
        if marker not in text:
            yield agent(f"{WORKFLOWS / name}: nothing {meaning} ('{marker}' absent)")


def check_merge_workflow(root: Path) -> Iterable[Finding]:
    """ADR 0021: a forge that runs ``ci`` has the workflow that merges what the judge passes."""
    unmet = "nothing merges a change the judge passes"
    return forge_workflow(root, MERGE_WORKFLOW, unmet=unmet, shape=MERGE_SHAPE)


def check_weekly_workflow(root: Path) -> Iterable[Finding]:
    """ADR 0025: a forge that runs ``ci`` schedules the weekly gates and files what they find."""
    unmet = "nothing runs the weekly gates, so a finding is seen only when someone thinks to look"
    return forge_workflow(root, WEEKLY_WORKFLOW, unmet=unmet, shape=WEEKLY_SHAPE)


def check_plugin_pin(root: Path) -> Iterable[Finding]:
    """The plugin version has one home, the binding; the workflows and the justfile follow it."""
    binding = read(root / "docs/process/binding.md")
    if binding is None:
        return
    pin = plugin_pin.pinned(binding)
    if pin is None:
        line = plugin_pin.line_for(plugin_pin.PLUGIN)
        yield Finding(
            "docs/process/binding.md",
            "no '- Ratchet plugin: <version> at <commit>' line; add under '## Runner', for the"
            f" plugin in use: {line}",
            Agent(start=(line,)),
        )
    yield from check_pin_recipe(root)
    if pin is None:
        return
    for name in (*TIER_WORKFLOWS, MERGE_WORKFLOW, WEEKLY_WORKFLOW):
        text = read(root / WORKFLOWS / name) or ""
        refs = set(PLUGIN_REF.findall(text))
        if not refs and "RATCHET_PLUGIN" in text:
            yield agent(
                f"{WORKFLOWS / name}: checks no plugin out by `repository:` then `ref:`, so "
                "nothing holds it to the commit the binding pins"
            )
        for ref in sorted(refs - {pin.commit}):
            yield agent(
                f"{WORKFLOWS / name}: checks the plugin out at '{ref}', not the commit the "
                f"binding pins ({pin.commit})"
            )


def check_pin_recipe(root: Path) -> Iterable[Finding]:
    justfile = read(root / "justfile")
    if justfile is not None and "plugin_pin.py" not in justfile:
        yield Finding(
            "justfile",
            "no tier runs plugin_pin.py, so a local run can use a plugin other than the one the"
            " binding pins; copy `plugin-pin` from the template justfile, as the first dependency"
            " of each tier",
            Agent(start=template_definition("plugin-pin")),
        )


def answered_gates(binding: str) -> set[str]:
    """The entry points of the Gates rows the binding answers with something other than none."""
    return {
        row.gate
        for row in project_bite.gate_rows(binding)
        if row.tool and not application_types.NONE.match(row.tool) and not later(row.tool)
    }


def defines_variable(justfile: str, name: str) -> bool:
    return (
        re.search(rf"^(?:export[ \t]+)?{re.escape(name)}[ \t]*:=", justfile, re.MULTILINE)
        is not None
    )


def check_tier_lists(root: Path, justfile: str) -> Iterable[Finding]:
    """A tier list the tier workflows read, which the justfile does not define."""
    for workflow, lists in TIER_LISTS.items():
        text = read(root / WORKFLOWS / workflow) or ""
        for name, meaning in lists:
            if f"--evaluate {name}" in text and not defines_variable(justfile, name):
                yield Finding(
                    "justfile",
                    f"has no `{name}` variable, which the `shape` job of `{workflow}` reads with"
                    f" `just --evaluate {name}`; add the template's, naming {meaning}",
                    Agent(start=template_definition(name)),
                )


def check_runner(root: Path) -> Iterable[Finding]:
    """What the template justfile's tiers rely on, read only where a justfile is the runner."""
    justfile = read(root / "justfile")
    if justfile is None:
        return
    yield from check_tier_lists(root, justfile)
    code = code_of(justfile)
    gates = answered_gates(read(root / "docs/process/binding.md") or "")
    if gates & OVER_THE_CHANGE and "changed.py" not in code:
        yield Finding(
            "justfile",
            "no gate runs through `changed.py`, so a gate over the change has no base on the"
            " adoption; add the template's `changed` variable and run diff-coverage, test-counts"
            " and mutation-changed as `{{changed}} -- <command>`, with `{base}` for the commit"
            " compared with",
            Agent(start=template_definition("changed")),
        )
    ratchet = "\n".join(definition(justfile, "ratchet"))
    if ratchet and "hooks-installed" not in ratchet and "hooks_installed.py" not in ratchet:
        yield Finding(
            "justfile",
            "the `ratchet` recipe raises the thresholds without asking whether a commit would run"
            " tier 0; make `just hooks-installed` its first line, as the template's is",
            Agent(start=template_definition("ratchet")),
        )
    if "--jobs" in code and "RATCHET_JOBS" not in code:
        yield Finding(
            "justfile",
            "passes `--jobs` without reading RATCHET_JOBS, the one number that caps the gates"
            " and the bite copies at once; use the template's `jobs` variable",
            Agent(start=template_definition("jobs")),
        )
    if gates & TIMED and "parallel.py" in code and "--alone" not in code:
        yield Finding(
            "justfile",
            "nothing runs `perf` and `load` alone, so other gates disturb the time they judge;"
            " add the template's `alone` variable and pass `{{alone}}` to `parallel.py`",
            Agent(start=template_definition("alone")),
        )
    if {"load-smoke", "flag-states"} <= gates and "--temporary" not in code:
        yield Finding(
            "justfile",
            "no gate that requests the deliverable's routes boots it with the temporary flags on"
            " (`flags.py --temporary`); add the template's `flags_on` and `probed`, bind"
            " `flags_on` to the flag seam's override, and boot load-smoke, api-deep, dast,"
            " pentest, load and load-soak through `{{probed}}`",
            Agent(start=template_definition("flags_on") + template_definition("probed")),
        )


def hook_source(path: Path) -> str | None:
    """The text of a hook mechanism's file, or of each file in its directory under its name."""
    if path.is_dir():
        files = [item for item in sorted(path.iterdir()) if item.is_file()]
        return "\n".join(f"{item.name}\n{read(item) or ''}" for item in files)
    return read(path)


def check_framework_hooks(text: str) -> Iterable[Finding]:
    """The ``pre-commit`` framework's config: the commit-msg stage installed, tree gates only."""
    if not (INSTALLS_COMMIT_MSG.search(text) and COMMIT_MSG_STAGE.search(text)):
        yield agent(
            f"{COMMIT_HOOKS}: no commit-msg hook installed, so a bad subject is first seen in CI"
        )
    if WHOLE_TIER0_ENTRY.search(text):
        yield agent(f"{COMMIT_HOOKS}: {WHOLE_TIER0}")


def check_other_hooks(name: str, text: str, install: str | None) -> Iterable[Finding]:
    """A mechanism other than the ``pre-commit`` framework: both hooks, tree gates, a command."""
    for hook in hooks_installed.HOOKS:
        if hook not in text:
            yield agent(
                f"{name}: names no `{hook}` hook, so a commit would skip that half of tier 0"
            )
    if RUNS_WHOLE_TIER0.search(text):
        yield agent(f"{name}: {WHOLE_TIER0}")
    if install is None:
        yield agent(
            "docs/process/binding.md: the `- Commit hooks:` line names no command after "
            "', installed by', so a red `hooks-installed` cannot say what the owner runs"
        )


def check_commit_hooks(root: Path) -> Iterable[Finding]:
    """The hooks the binding names exist and run tier 0 in its two stages; the probe proves it."""
    name, install = hooks_installed.mechanism(root)
    text = hook_source(root / name)
    if text is None:
        yield agent(f"{name}: missing; the tier 0 commit hooks are installed from it")
        return
    if name == str(COMMIT_HOOKS):
        yield from check_framework_hooks(text)
    else:
        yield from check_other_hooks(name, text, install)
    justfile = read(root / "justfile")
    if justfile is not None and hooks_installed.PROBE_ENV not in justfile:
        yield agent(
            f"justfile: the `pre-commit` and `commit-msg` recipes do not answer "
            f"{hooks_installed.PROBE_ENV}, so `hooks_installed.py` cannot tell whether a commit "
            f"would run tier 0; copy them from {hooks_installed.TEMPLATE}"
        )


def check_settings(root: Path) -> Iterable[Finding]:
    if (root / settings_rules.MANIFEST).is_file():
        for line in settings_rules.drift(root):
            yield agent(line)


def check_quality(root: Path) -> Iterable[Finding]:
    for name in ("thresholds", "mutation-register"):
        path = root / "quality" / f"{name}.toml"
        if not path.is_file():
            yield agent(f"quality/{name}.toml: missing")
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            yield agent(f"quality/{name}.toml: {error}")
            continue
        if name == "mutation-register":
            yield from check_register(data)
    if not (root / "quality/snapshots").is_dir():
        yield agent("quality/snapshots: missing")


def check_register(data: dict[str, object]) -> Iterable[Finding]:
    survivors = data.get("survivor", [])
    if not isinstance(survivors, list):
        yield agent("quality/mutation-register.toml: 'survivor' is not an array of tables")
        return
    for index, entry in enumerate(survivors):
        for key in ("tool", "identity", "reason", "date"):
            if key not in entry:
                yield agent(f"quality/mutation-register.toml: survivor {index} lacks '{key}'")
        if len(str(entry.get("reason", ""))) < MIN_REGISTER_REASON:
            yield agent(
                f"quality/mutation-register.toml: survivor {index} needs a reason of "
                f"{MIN_REGISTER_REASON}+ characters"
            )


def check_bites(root: Path) -> Iterable[Finding]:
    # Why: ADR 0020. The method ships no tool, so the repository shows each gate it bound going
    # red on a planted fault; a gate with no bite, or a collection never shown empty, is unproven.
    return (agent(line) for line in project_bite.failures(root))


def check_incidents(root: Path) -> Iterable[Finding]:
    """Each record under ``docs/incidents/`` names a fault in the bite registry that reproduces it.

    Why: ADR 0028. An incident that taught the repository something must leave a gate that fails on
    it, so the record carries a line ``bite: <gate>/<name>``, the gate's entry point and the name
    of one ``[[bite]]`` of ``quality/bites.toml``.
    """
    records = sorted((root / INCIDENTS).glob("*.md"))
    registry, _ = project_bite.read_registry(read(root / project_bite.REGISTRY) or "")
    held = {f"{bite.gate}/{bite.name}" for bite in registry.bites}
    for path in records:
        named = INCIDENT_BITE.findall(path.read_text(encoding="utf-8"))
        where = path.relative_to(root)
        if not named:
            yield agent(
                f"{where}: names no bite; add a line `bite: <gate>/<name>` for a [[bite]] of"
                f" {project_bite.REGISTRY} that reproduces it"
            )
        for name in named:
            if name not in held:
                yield agent(
                    f"{where}: names bite '{name}', which {project_bite.REGISTRY} does not hold"
                )


def check_disputes(root: Path) -> Iterable[Finding]:
    directory = root / "docs/disputes"
    if not directory.is_dir():
        yield agent("docs/disputes: missing")
        return
    for _, path in numbered_files(directory):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^- Status: (\S+)$", text, re.MULTILINE)
        if match is None or match.group(1) not in DISPUTE_STATUSES:
            yield agent(f"{path.relative_to(root)}: needs '- Status: open|upheld|rejected'")
        for section in DISPUTE_SECTIONS:
            if section not in text:
                yield agent(f"{path.relative_to(root)}: has no '{section}' section")


def check_features(root: Path) -> Iterable[Finding]:
    # Why: the pipeline's spec role writes docs/features/<slug>/spec.md; the directory is the
    # method's. The feature's input is a tracker ticket, which the feature preflight checks.
    if not (root / "docs/features").is_dir():
        yield agent("docs/features: missing")


def check_pr_template(root: Path) -> Iterable[Finding]:
    relative = pr_template_path(root)
    text = read(root / relative)
    if text is None:
        yield agent(f"{relative}: missing (the binding names the pull request template)")
        return
    for heading in PR_SECTIONS:
        if not re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE):
            yield agent(f"{relative}: has no '{heading}' section")


# Why: the pin comes first, since an upgrade reads the plugin's commit from it.
CHECKS: tuple[Check, ...] = (
    check_plugin_pin,
    check_agent_instructions,
    check_glossary,
    check_brief,
    check_adrs,
    check_binding,
    check_triggers,
    check_operability,
    check_speed,
    check_manifest,
    check_doors,
    check_merge_workflow,
    check_weekly_workflow,
    check_settings,
    check_commit_hooks,
    check_runner,
    check_quality,
    check_bites,
    check_incidents,
    check_disputes,
    check_features,
    check_pr_template,
)


def findings(root: Path) -> list[Finding]:
    """Every failure of every check, in the order of ``CHECKS``."""
    return [failure for check in CHECKS for failure in check(root)]


def failures(root: Path) -> list[str]:
    return [str(finding) for finding in findings(root)]


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
        *pin_mutations(),
        *weekly_mutations(),
        *record_mutations(),
        *trigger_mutations(),
        *upgrade_mutations(),
    ]


def trigger_mutations() -> list[Mutation]:
    """The `later:` cells, the Life events line, the Triggers table and the incident records."""
    binding = "docs/process/binding.md"
    later = "| rolls back safely | later: life:deployed and code:stored-format |"

    def incident(text: str) -> Callable[[Path], None]:
        def mutate(root: Path) -> None:
            (root / INCIDENTS).mkdir(parents=True)
            (root / INCIDENTS / "2026-01-01-outage.md").write_text(text, encoding="utf-8")

        return mutate

    return [
        (
            "later: row naming another question's trigger",
            rewrite(binding, later, "| rolls back safely | later: code:outbound-call |"),
        ),
        (
            "later: row of a question asked from the first commit",
            rewrite(binding, "| format | a tool |", "| format | later: code:source |"),
        ),
        ("binding without a Life events line", rewrite(binding, "- Life events: none\n", "")),
        (
            "binding declaring a life event the catalogue lacks",
            rewrite(binding, "- Life events: none", "- Life events: famous"),
        ),
        (
            "binding without a Triggers section",
            rewrite(binding, "## Triggers\n", "## Hunches\n"),
        ),
        (
            "Triggers table without a kind a question is triggered by",
            rewrite(binding, "| config-read | none: the self-test has none | |\n", ""),
        ),
        (
            "Triggers row naming a kind the catalogue lacks",
            rewrite(binding, "| flag | none:", "| gizmo | none:"),
        ),
        (
            "Triggers row with a pattern that is not a regex",
            rewrite(binding, "| flag | none: the self-test has none | |", "| flag | x | ( |"),
        ),
        (
            "binding without the incidents row",
            rewrite(binding, "| incidents tighten a gate |", "| incidents |"),
        ),
        ("incident record naming no bite", incident("# An outage\n\nIt went down.\n")),
        ("incident record naming a bite that is not held", incident("bite: lint/never-planted\n")),
    ]


def upgrade_mutations() -> list[Mutation]:
    """What an adoption made by an earlier plugin lacks, each a red line of its own."""
    return [
        ("AGENTS.md without the Asking the owner section", rewrite("AGENTS.md", ASKING, "## Ask")),
        (
            "binding without the tier0-tree row",
            rewrite("docs/process/binding.md", "| tier0-tree | `just tier0-tree` |\n", ""),
        ),
        (
            "binding without a row the catalogue gained",
            rewrite(
                "docs/process/binding.md",
                "| backup restores | a tool | weekly | | `just restore-drill` |\n",
                "",
            ),
        ),
        (
            "binding without the triggers entry point",
            rewrite("docs/process/binding.md", "| triggers | `just triggers` |\n", ""),
        ),
        ("justfile without a recipe the binding names", rewrite("justfile", "bite-one gate:", "")),
        (
            "justfile with no gate run through changed.py",
            rewrite("justfile", "/scripts/changed.py", "/scripts/base.py"),
        ),
        (
            "justfile whose ratchet recipe does not ask whether hooks run",
            rewrite("justfile", "ratchet:\n    just hooks-installed\n", "ratchet:\n"),
        ),
        (
            "justfile capping the gates at once by another variable",
            rewrite("justfile", '"RATCHET_JOBS"', '"JOBS"'),
        ),
        (
            "justfile timing perf and load beside other gates",
            rewrite("justfile", 'alone := "--alone perf load"', 'alone := ""'),
        ),
        (
            "justfile booting the route gates with the defaults only",
            rewrite("justfile", "flags.py --temporary on", "every flag on"),
        ),
        (
            "justfile without the list of tier 1 gates the workflow reads",
            rewrite("justfile", "tier1_gates :=", "tier1_list :="),
        ),
        (
            "justfile without the list of sharded tier 2 gates the workflow reads",
            rewrite("justfile", "tier2_sharded :=", "tier2_shard :="),
        ),
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
        (
            "bound gate without the proof its question requires",
            reregister('\nproof = "outside-the-seam"\n', "\n"),
        ),
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
    ci, deep = str(WORKFLOWS / "ci.yml"), str(WORKFLOWS / "deep.yml")
    return [
        ("CI whose ceilings block a pull request", rewrite(ci, ' --over "$OVER"', "")),
        ("nightly that holds no job to its tier's ceiling", rewrite(deep, " --tier tier2", "")),
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

    binding, named = "docs/process/binding.md", f"- Commit hooks: `{COMMIT_HOOKS}`"
    renamed = rewrite(binding, named, "- Commit hooks: `lefthook.yml`, installed by `lefthook`")

    def another_mechanism_with_one_hook(root: Path) -> None:
        renamed(root)
        (root / "lefthook.yml").write_text("pre-commit:\n  commands:\n    tree: {run: just x}\n")

    return [
        ("binding naming commit hooks that are not there", renamed),
        ("another hook mechanism without the commit-msg hook", another_mechanism_with_one_hook),
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


def weekly_mutations() -> list[Mutation]:
    def no_weekly_workflow(root: Path) -> None:
        (root / WORKFLOWS / WEEKLY_WORKFLOW).unlink()

    workflow = str(WORKFLOWS / WEEKLY_WORKFLOW)
    return [
        ("forge without the weekly workflow", no_weekly_workflow),
        ("weekly workflow that runs on no schedule", rewrite(workflow, "schedule:", "push:")),
        (
            "weekly workflow that cannot open an issue",
            rewrite(workflow, "issues: write", "issues: read"),
        ),
        (
            "binding without the weekly entry point",
            rewrite("docs/process/binding.md", "| weekly | `just weekly` |\n", ""),
        ),
    ]


def pin_mutations() -> list[Mutation]:
    binding, pinned = "docs/process/binding.md", "0" * 40
    return [
        (
            "binding with the plugin pin unfilled",
            rewrite(binding, f"0.0.0 at {pinned}", PLUGIN_PIN_PLACEHOLDER),
        ),
        (
            "workflow at a commit the binding does not pin",
            rewrite(str(WORKFLOWS / "deep.yml"), pinned, "1" * 40),
        ),
        (
            "workflow that checks no plugin out by commit",
            rewrite(str(WORKFLOWS / "ci.yml"), f'ref: "{pinned}", ', ""),
        ),
        (
            "justfile whose tiers skip the plugin pin",
            rewrite("justfile", "plugin_pin.py", "pin.py"),
        ),
    ]


def record_mutations() -> list[Mutation]:
    def adr_without_refuter(root: Path) -> None:
        (root / "docs/adr/0002-plain.md").write_text("# We keep it plain\n\nBecause.\n")

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
        ("features directory removed", features_directory_removed),
        ("ADR numbering with a gap", gap_adrs),
        ("dispute with an unknown status", bad_dispute),
        ("register entry with a short reason", short_register_reason),
    ]


UNBOUND_GROUP = re.compile(r"paths = \[\n(?:\s*# bind:[^\n]*\n)+\]")
TYPES_PLACEHOLDER = re.compile(r"^- Application types: <[^\n]*>$", re.MULTILINE)
EMPTY_TOOL_CELL = re.compile(r"^(\| [^|\n]+ \|) \|", re.MULTILINE)
TRIGGERS_PLACEHOLDER = re.compile(r"^\| <kind> \|[^\n]*$", re.MULTILINE)
SPEED_PLACEHOLDER = re.compile(r"^(- [A-Z][a-z ]+: )<[^\n]*>$", re.MULTILINE)


def fill_binding(text: str) -> str:
    """A command line tool's binding: one row its type leaves out, a tool in every other."""
    text = text.replace(PLUGIN_PIN_PLACEHOLDER, f"0.0.0 at {'0' * 40}")
    text = TYPES_PLACEHOLDER.sub("- Application types: cli", text)
    text = text.replace("| contract drift | |", "| contract drift | none: not a service |")
    # Why: ADR 0028. One row waits on its trigger and so needs no bite; the rest are bound.
    text = text.replace(
        "| rolls back safely | |",
        "| rolls back safely | later: life:deployed and code:stored-format |",
    )
    text = TRIGGERS_PLACEHOLDER.sub(triggers_rows, text)
    text = EMPTY_TOOL_CELL.sub(r"\1 a tool |", text)
    return SPEED_PLACEHOLDER.sub(r"\1stated for this stack", text)


def triggers_rows(_: re.Match[str]) -> str:
    """A row for each kind of code a question asked of a command line tool is triggered by."""
    kinds = sorted(application_types.load().kinds_asked({"cli"}))
    return "\n".join(f"| {kind} | none: the self-test has none | |" for kind in kinds)


def fill_registry(binding: str) -> str:
    """A fault per gate the filled binding binds, each collection emptied, each proof claimed."""
    catalogue = application_types.load()
    entries = []
    for row in project_bite.gate_rows(binding):
        if not row.bound:
            continue
        head = f'[[bite]]\ngate = "{row.gate}"\n'
        fault = 'write = { path = "src/bite", content = "a fault" }\nexpect = "a fault"\n'
        entries.append(f'{head}name = "a fault"\n{fault}')
        if row.question in catalogue.collections:
            emptied = 'remove = ["collection/**"]\nexpect = "nothing to run"\n'
            entries.append(f'{head}name = "nothing to run"\n{emptied}')
        entries += [
            f'{head}name = "{proof}"\nproof = "{proof}"\n{fault}'
            for proof in catalogue.proofs.get(row.question, ())
        ]
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
        "GLOSSARY.md": {GLOSSARY_PLACEHOLDER: "the project"},
        str(WORKFLOWS / "ci.yml"): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
        str(WORKFLOWS / "deep.yml"): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
        str(WORKFLOWS / MERGE_WORKFLOW): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
        str(WORKFLOWS / WEEKLY_WORKFLOW): {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40},
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
    if argv[1:2] == ["--incidents"]:
        # Why: the binding's `incidents` gate is this one check, so a red record is named alone.
        root = Path(argv[2]) if argv[2:] else Path()
        found = list(check_incidents(root.resolve()))
        for line in found:
            print(line)
        print(f"incidents: {'red' if found else 'green'} ({len(found)} failures)")
        return 1 if found else 0
    root = Path(argv[1]) if len(argv) > 1 else Path()
    return run(root.resolve())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
