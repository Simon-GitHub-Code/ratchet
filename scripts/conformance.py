#!/usr/bin/env python3
"""Method conformance gate: does a repository still carry the Ratchet method's artefacts?

Usage: ``conformance.py [ROOT]`` exits 1 and prints one line per failure. ``conformance.py
--self-test`` builds repositories from the init and adopt templates, expects the pristine one
to pass and each mutated one to fail, so the checker is shown to bite.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
import tempfile
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import application_types
import architecture
import change_size
import gatelib
import hooks_installed
import installs
import justfile_text
import markdown_text
import owed
import pins
import plugin_pin
import project_bite
import settings_rules
import suppressions
import triggers
from ratchet_guards import committed

# Why: the dependency contract lives with the guard, under hooks/, as gatelib lives here for it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import dependencies
from ratchet_guards import manifest as guard_manifest

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
DOOR_MEANINGS = {
    "change": "a path whose every change is final, such as a migration or infrastructure code",
    "rewrite": "a file the default branch holds that is never rewritten, such as a golden fixture",
    "removal": "a path whose loss breaks a consumer, such as a contract or a public snapshot",
}
# Why: ADR 0037. What holds while the owner has not answered, in his words: the upgrade asks him
# nothing, so each default is the one that loosens no gate.
UNSTATED_STAGE = (
    "Whether this project is live, relied on by other people, or holds data that cannot be"
    " replaced is not written down yet. Until it is, Ratchet decides which changes may merge"
    " without you as carefully as it would for a live project."
)
NO_DOOR = (
    "No part of this project is marked as impossible to take back once it ships, such as a"
    " database change, data saved in an older format, or something other programs rely on."
    " Until one is, an agent can merge a change to such a part without you. To mark one, ask an"
    " agent which files hold it: it adds them to guards/manifest.toml on a branch, and you decide"
    " at that merge."
)
WEEKLY_CHECKS = "the weekly checks"
SHARD_MATRIX = "matrix.shard"
# Why: a budget call as the templates before ADR 0025 wrote it; the upgrade adds the flags to it.
OLD_TIER0_BUDGET = (
    'python3 "$RATCHET_PLUGIN/scripts/budget.py" tier0 --report .ratchet/timing.jsonl -- just tier0'
)
OLD_GATE_BUDGET = (
    'python3 "$RATCHET_PLUGIN/scripts/budget.py" "${{ matrix.gate }}" --report'
    ' .ratchet/timing.jsonl -- just "${{ matrix.gate }}"'
)
PLUGIN_ENV = "  RATCHET_PLUGIN: "
TOP_KEY = re.compile(r"^ {0,2}\S")
MATRIX_GATE = re.compile(r"^\s+gate:(?:\s|$)")
GATE_NAME = re.compile(r"[\w-]+")
HELD_TO_TIER = "holds a gate's job to its tier's ceiling"
TRIGGERS_PLACEHOLDER_ROW = (
    "| <kind> | <globs of the tracked files that hold one, or `none: <reason>`>"
    " | <optional regex> |"
)
# Why: adopt's step 4: `flag` is `flags.toml` with the pattern of a flag's table in every stack.
MECHANICAL_TRIGGERS = {"flag": "| flag | `flags.toml` | `^\\[\\[flag\\]\\]` |"}
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
BINDING_PATH = str(gatelib.BINDING)
ENTRY_POINTS_SECTION = "## Entry points"
TESTS_SECTION = "## Tests"
CHANGE_CEILING = "[ceilings.change]"
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
PIN_RECIPE = "plugin-pin"
PIN_SCRIPT = "plugin_pin.py"
TOOLCHAIN_MARKER = "# bind: toolchain setup"
TOOLCHAIN_PLACEHOLDER = f"      {TOOLCHAIN_MARKER}, pinned, with the setup action's own cache on"
TOOLCHAIN_STEP = "      - run: ./scripts/install-toolchain"
RULE_VERBS = {
    "Bash": ("runs", "run"),
    "Read": ("reads", "read"),
    "Edit": ("changes", "change"),
    "Write": ("writes", "write"),
}
WORKFLOW_JOB = re.compile(r"^  ([A-Za-z_][\w-]*):[ \t]*(?:#.*)?$")
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
LOCKED_PATTERNS = {".claude/settings.json", ".claude/hooks/**"}
# Why: the hook and the judge read the manifest as the default branch holds it, so a change to it
# needs no lock, only to stay a guard path the judge sends to the owner (ADR 0033).
GUARDED_PATTERNS = {"guards/**"}
# Why: a repository with no forge names a change template; adopt's own wording for it follows
# the path with a clause, so the line is read up to the closing backtick.
PR_TEMPLATE_LINE = re.compile(r"^- (?:Pull request|Change) template: `([^`]+)`", re.MULTILINE)
DEFAULT_PR_TEMPLATE = ".github/pull_request_template.md"
NUMBERED = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
MIN_REASON = 10
MIN_REGISTER_REASON = 20
TEMPLATES = Path(__file__).resolve().parents[1] / "skills"
ADOPT_TEMPLATES = TEMPLATES / "adopt/templates"
INIT_TEMPLATES = TEMPLATES / "init/templates"
AGENTS_TEMPLATE = INIT_TEMPLATES / "AGENTS.md"
ASKING = "## Asking the owner"
RUNS_JUST = re.compile(r"^just\s+@?([A-Za-z_][\w-]*)")
# Why: a gate over the change compares with a base; `changed.py` hands it one, and judges nothing
# on the adoption, whose base holds no binding.
OVER_THE_CHANGE = frozenset({"diff-coverage", "test-counts", "mutation-changed"})
# Why: these gates judge elapsed time, which a gate running beside them disturbs.
TIMED = frozenset({"perf", "load"})


@dataclass(frozen=True)
class Auto:
    """A fix the upgrade makes itself: one line for the report, and the text it makes of a file.

    Only ``tried`` makes one, from an ``Edit`` its check offered.
    """

    does: str
    want: gatelib.Want


@dataclass(frozen=True)
class Agent:
    """A fix that needs judgement: the template text to start from, and what an answer asks next."""

    start: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


@dataclass(frozen=True)
class Owner:
    """A call only the owner makes, and what holds until they make it.

    ``assumed`` is the default the upgrade leaves in place, in plain words for the owner: one
    that loosens no gate and removes nothing of theirs, so the upgrade asks nothing (ADR 0037).
    ``ask`` is the question that settles it, with what the repository shows and what follows.
    """

    ask: str
    assumed: str
    evidence: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edit:
    """An edit a check offers, and who fixes the finding when the edit does not clear it."""

    does: str
    want: gatelib.Want
    otherwise: Agent | Owner


Fix = Auto | Edit | Agent | Owner


@dataclass(frozen=True)
class Finding:
    """One thing the repository lacks: the file, what is wrong, and who fixes it.

    ``rule`` names the check that raised it, which ``raised`` sets. ``identity`` is what stays the
    same while the finding stands, where the problem says more: a line number, or advice that
    changes as other findings are fixed. An owed entry matches ``key`` (ADR 0037). ``stops`` names
    the check that does not run while the finding stands, as the owner reads it.
    """

    path: str
    problem: str
    fix: Fix
    rule: str = ""
    identity: str = ""
    stops: str = ""

    def __post_init__(self) -> None:
        """Refuse an edit under ``guards/``: the manifest says who may edit those paths."""
        if isinstance(self.fix, Auto | Edit) and under_guards(self.path):
            message = f"{self.path}: an upgrade never writes under guards/ by itself"
            raise ValueError(message)

    def __str__(self) -> str:
        """The one line the conformance gate prints."""
        return f"{self.path}: {self.problem}"

    def key(self) -> owed.Key:
        """What an owed entry must hold to excuse this finding: its check, file and identity."""
        return (self.rule, self.path, self.identity or self.problem)


Check = Callable[[Path], Iterable[Finding]]


def under_guards(path: str) -> bool:
    """Whether ``path``, relative to the repository, is under ``guards/``."""
    return PurePosixPath(path).parts[:1] == ("guards",)


def agent(line: str, start: tuple[str, ...] = (), then: tuple[str, ...] = ()) -> Finding:
    """A finding an agent fixes, from a check's one line: every line starts with its path."""
    path, _, problem = line.partition(": ")
    return Finding(path, problem, Agent(start, then))


def owner(
    line: str,
    ask: str,
    *,
    assumed: str,
    evidence: tuple[str, ...] = (),
    then: tuple[str, ...] = (),
) -> Finding:
    """A finding only the owner settles, from a check's one line, its question and its default."""
    path, _, problem = line.partition(": ")
    return Finding(path, problem, Owner(ask, assumed, evidence, then))


@contextlib.contextmanager
def made(tree: Path, name: str, want: gatelib.Want) -> Iterator[bool]:
    """The tree with ``want`` made of its file ``name`` until the block ends; whether it changed.

    The file is followed through links inside the tree. It is never written outside the tree or
    under ``guards/``, and never when it is not UTF-8. When the block ends the file holds its
    bytes again, or is gone with the directories made for it.
    """
    home, target = tree.resolve(), (tree / name).resolve()
    if not target.is_relative_to(home) or under_guards(str(target.relative_to(home))):
        yield False
        return
    data = target.read_bytes() if target.is_file() else None
    try:
        after = gatelib.edited(want, data)
    except UnicodeDecodeError:
        after = None
    if after is None:
        yield False
        return
    created = [parent for parent in target.parents if not parent.exists()]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(after)
    try:
        yield True
    finally:
        if data is not None:
            target.write_bytes(data)
        elif created:
            shutil.rmtree(created[-1])
        else:
            target.unlink()


def tried(tree: Path, check: Check, finding: Finding) -> Finding:
    """The finding with its offered edit made ``Auto`` or ``otherwise``, as the check then says.

    Why: an edit counts as the fix only when the check that raised the finding, run again on the
    tree the edit made, no longer raises it. A test of the edit's own could be weaker than its
    finding, and a gate the upgrade claims to have fixed would stay loose without a sound.
    """
    fix = finding.fix
    if not isinstance(fix, Edit):
        return finding
    with made(tree, finding.path, fix.want) as changed:
        cleared = changed and all(
            (again.path, again.problem) != (finding.path, finding.problem) for again in check(tree)
        )
    return replace(finding, fix=Auto(fix.does, fix.want) if cleared else fix.otherwise)


def read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def justfile_paths(root: Path) -> list[Path]:
    """The justfile and every file it imports that exists, in the order ``just`` reads them."""
    found: list[Path] = []
    waiting = [root / "justfile"]
    while waiting:
        path = waiting.pop(0)
        if not path.is_file() or path.resolve() in {known.resolve() for known in found}:
            continue
        found.append(path)
        text = read(path) or ""
        waiting += [path.parent / Path(name).expanduser() for name in justfile_text.imports(text)]
    return found


def read_justfile(root: Path) -> str | None:
    """The justfile and every file it imports, in the order ``just`` reads them, as one text.

    Why: ``just`` reads an imported recipe as the justfile's own, so a check that read the
    justfile alone would ask for a recipe it already has, and an edit would define it twice.
    """
    texts = [read(path) or "" for path in justfile_paths(root)]
    return "\n".join(texts) if texts else None


def copied(root: Path, target: Path) -> dict[str, str]:
    """``gatelib.copy_seen``, with every file of the repository the justfile imports.

    Why: ``just`` reads an import git ignores, such as an optional ``import? 'local.just'``. A copy
    without it would judge the justfile as ``just`` never reads it, and copy a recipe that file
    already defines.
    """
    foreign = gatelib.copy_seen(root, target)
    home = root.resolve()
    for path in justfile_paths(root):
        lands = path.resolve()
        name = os.path.relpath(path, root)
        if lands.is_relative_to(home) and not os.path.lexists(target / name):
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lands, target / name)
    return foreign


def template_file(path: str) -> str:
    """A file of the adopt templates, or of the init templates when adopt has none."""
    return read(ADOPT_TEMPLATES / path) or read(INIT_TEMPLATES / path) or ""


def headings(text: str) -> tuple[str, ...]:
    """The ``## `` headings of a Markdown text, in order."""
    return tuple(line.rstrip() for line in text.splitlines() if line.startswith("## "))


def whole_file(path: str, problem: str, template: str | None = None) -> Finding:
    """A file missing that the template supplies whole, under ``template`` or its own path."""
    want = gatelib.created(template_file(template or path))
    return Finding(path, problem, Edit("add the template's file", want, Agent()))


def kept_directory(path: str) -> Finding:
    """A directory the method keeps, which the template supplies as an empty ``.gitkeep``."""
    return whole_file(f"{path}/.gitkeep", f"the directory {path} is missing")


def missing_section(path: str, problem: str, heading: str, *, template_text: str) -> Finding:
    """A ``## `` section the template has and the file lacks: the upgrade adds it verbatim."""
    block = heading + markdown_text.section(template_text, heading).rstrip()
    want = markdown_text.with_section(heading, block, headings(template_text))
    start = (heading, *block.splitlines()[1:])
    return Finding(
        path, problem, Edit(f"add the template's '{heading}' section", want, Agent(start))
    )


def template_definition(name: str) -> tuple[str, ...]:
    return justfile_text.definition(template_file("justfile"), name)


def check_agent_instructions(root: Path) -> Iterable[Finding]:
    name = "AGENTS.md"
    text = read(root / name)
    if text is None:
        name, text = "CLAUDE.md", read(root / "CLAUDE.md")
    if text is None:
        yield agent("AGENTS.md (or CLAUDE.md): missing")
    elif "ratchet:method" not in text:
        yield agent("AGENTS.md: does not tell agents to load the ratchet:method skill")
    elif not markdown_text.has_section(text, ASKING):
        problem = f"has no '{ASKING}' section; copy it from the template AGENTS.md"
        yield missing_section(name, problem, ASKING, template_text=read(AGENTS_TEMPLATE) or "")


def check_glossary(root: Path) -> Iterable[Finding]:
    if (root / "GLOSSARY-MAP.md").is_file():
        return
    text = read(root / "GLOSSARY.md")
    if text is None:
        yield agent("GLOSSARY.md (or GLOSSARY-MAP.md): missing")
    elif not markdown_text.has_section(text, "## Language"):
        yield agent("GLOSSARY.md: has no '## Language' section")
    elif GLOSSARY_PLACEHOLDER in text:
        yield agent(f"GLOSSARY.md: still names '{GLOSSARY_PLACEHOLDER}'; name the project")


def check_brief(root: Path) -> Iterable[Finding]:
    text = read(root / "docs/design-brief.md")
    if text is None:
        yield agent("docs/design-brief.md: missing")
        return
    lines = text.splitlines()
    for heading in ("## Slice one", "## Explicitly out"):
        if not any(line.startswith(heading) for line in lines):
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
    if not markdown_text.has_section(text, REFUTATION_SECTION):
        yield agent(f"{rel}: has no '{REFUTATION_SECTION}' section")
    elif markdown_text.empty_body(text, REFUTATION_SECTION):
        yield agent(f"{rel}: '{REFUTATION_SECTION}' names no observation that would show it wrong")
    if number == 1 and "Ratchet method" not in text:
        yield agent(f"{rel}: ADR 0001 records adopting the Ratchet method")


def template_binding() -> str:
    return template_file(BINDING_PATH)


def template_rows() -> dict[str, tuple[str, str]]:
    """The template binding's Entry points rows by name, in its order: the row and its command."""
    body = markdown_text.section(template_binding(), ENTRY_POINTS_SECTION)
    rows = dict(markdown_text.table_cells(body))
    return {
        match.group(1): (line, match.group(2))
        for line in body.splitlines()
        if (match := markdown_text.TABLE_ROW.match(line)) and match.group(1) in rows
    }


def template_gate_row(question: str) -> str:
    """The template binding's Gates row for a question (a test holds that it has one)."""
    body = markdown_text.section(template_binding(), "## Gates")
    prefix = f"| {question} |"
    return next(line for line in body.splitlines() if line.startswith(prefix))


def entry_points(text: str) -> dict[str, str]:
    """The Entry points table of a binding: each entry point and its command."""
    return dict(markdown_text.table_cells(markdown_text.section(text, ENTRY_POINTS_SECTION)))


def entry_row(name: str, rows: dict[str, tuple[str, str]], *, on_just: bool) -> Finding:
    """A missing Entry points row, which the upgrade adds when the runner is ``just``."""
    row = rows[name][0]
    problem = (
        f"the Entry points table has no row for '{name}'; add the template's row {row},"
        " with your runner's command"
    )
    if not on_just:
        return Finding(BINDING_PATH, problem, Agent(start=(row,)))
    want = markdown_text.with_row(ENTRY_POINTS_SECTION, row, tuple(rows))
    edit = Edit(f"add the template's row for '{name}'", want, Agent(start=(row,)))
    return Finding(BINDING_PATH, problem, edit)


def missing_recipe(justfile: str, recipe: str, name: str) -> Finding:
    """A recipe an entry point runs and the justfile lacks, copied when it stands on its own."""
    text = template_file("justfile")
    start = justfile_text.definition(text, recipe)
    problem = (
        f"has no `{recipe}` recipe, which the binding's entry point '{name}' runs; copy it from"
        " the template justfile"
    )
    reasons = justfile_text.unmet(text, justfile, recipe)
    if reasons:
        return Finding(
            "justfile",
            f"{problem}; it cannot be copied as it stands: {'; '.join(reasons)}",
            Agent(start=start),
            identity=problem,
            stops=WEEKLY_CHECKS if recipe == "weekly" else "",
        )
    want = justfile_text.with_recipe(text, recipe)
    edit = Edit(f"copy the template's `{recipe}` recipe", want, Agent(start=start))
    return Finding("justfile", problem, edit)


def check_entry_points(root: Path, text: str) -> Iterable[Finding]:
    """Every entry point has a row naming its command, and a ``just`` command has its recipe.

    A command that names a module the justfile declares with ``mod`` runs that module's recipe,
    which this check does not read.
    """
    commands = entry_points(text)
    rows = template_rows()
    on_just = all(command.strip("`").split()[:1] == ["just"] for command in commands.values())
    justfile = read_justfile(root)
    for name in ENTRY_POINTS:
        command = commands.get(name)
        if command is None:
            command = rows[name][1]
            yield entry_row(name, rows, on_just=on_just)
        elif not command or "<" in command:
            yield agent(f"{BINDING_PATH}: entry point '{name}' names no command")
            continue
        recipe = RUNS_JUST.match(command.strip("`"))
        if justfile is None or recipe is None or recipe.group(1) in justfile_text.modules(justfile):
            continue
        if not justfile_text.defines_recipe(justfile, recipe.group(1)):
            yield missing_recipe(justfile, recipe.group(1), name)


def check_binding(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None:
        yield agent(f"{BINDING_PATH}: missing")
        return
    yield from check_entry_points(root, text)
    if not markdown_text.has_section(text, TESTS_SECTION):
        # Why: ADR 0032. The roles that write tests read the test runner and the directories from
        # this section; the upgrade adds the template's, which an agent then fills in.
        problem = (
            f"{BINDING_PATH}: has no '{TESTS_SECTION}' section, where the test runner and the"
            " directories tests live in are named"
        )
        yield missing_section(
            BINDING_PATH, problem, TESTS_SECTION, template_text=template_binding()
        )
    paths, problems = change_size.test_paths(text)
    if paths is None and problems[0].startswith("has no"):
        # Why: ADR 0045. The change-size gate counts tests against a ceiling of their own, and the
        # plugin names no stack, so which paths are tests is a section the project's agent writes.
        problem = f"{problems[0]}, which names the files that are tests (ADR 0045)"
        yield missing_section(
            BINDING_PATH, problem, change_size.SECTION, template_text=template_binding()
        )
    else:
        for problem in problems:
            yield agent(f"{BINDING_PATH}: {problem}")
    gates = markdown_text.section(text, "## Gates")
    if not gates:
        yield agent(f"{BINDING_PATH}: has no '## Gates' section")
        return
    # Why: ADR 0017. The types decide which rows apply; a row that applies is bound or an
    # accepted gap, and one that does not may say which type the repository is not.
    adrs = {number for number, _ in numbered_files(root / "docs/adr")}
    catalogue = application_types.load()
    rows = markdown_text.table_cells(gates)
    # Why: a row the catalogue gained since the adoption is an upgrade item with its template row.
    dropped = {
        application_types.dropped(question): question
        for question in sorted(catalogue.questions - {question for question, _ in rows})
    }
    typing = catalogue.types_problem(text)
    fired = Fired.of(root, text) if dropped else Fired({}, frozenset())
    for failure in catalogue.failures(text, rows, adrs):
        question = dropped.get(failure)
        if question is not None:
            trigger = catalogue.trigger_of(question)
            yield dropped_row(failure, question, trigger=trigger, fired=fired)
        elif failure == typing:
            yield agent(f"{BINDING_PATH}: {failure}", then=triggers_follow(catalogue))
        else:
            yield agent(f"{BINDING_PATH}: {failure}")


def check_architecture(root: Path) -> Iterable[Finding]:
    """The binding's Architecture section and the decision record it names (ADR 0049).

    Why: the owner chooses the architecture and its tools at adoption, and the gates and the
    review hold the parts it declares. A choice with no record, no alternatives, no reversal cost
    or no parts is one nobody can judge later, so each is red; the check reads structure only.
    """
    text = read(root / BINDING_PATH)
    if text is None:
        return
    if not markdown_text.has_section(text, architecture.SECTION):
        problem = (
            f"has no '{architecture.SECTION}' section, which names the architecture decision"
            " record and declares the parts (ADR 0049)"
        )
        yield missing_section(
            BINDING_PATH, problem, architecture.SECTION, template_text=template_binding()
        )
        return
    declared = architecture.declared(text)
    for problem in declared.problems:
        yield agent(f"{BINDING_PATH}: {problem}")
    if declared.record is None:
        return
    parts = PurePosixPath(declared.record).parts
    inside = parts[:2] == ("docs", "adr") and ".." not in parts
    record = read(root / declared.record) if inside else None
    if record is None:
        yield agent(
            f"{BINDING_PATH}: the architecture decision record {declared.record} is not a record"
            " under docs/adr/; write it as the method's architecture page says"
        )
        return
    for problem in architecture.record_problems(record):
        yield agent(f"{declared.record}: {problem}")


@dataclass(frozen=True)
class Fired:
    """What says whether a trigger has fired here: each kind's instances, the declared events."""

    found: dict[str, list[str]]
    events: frozenset[str]

    @classmethod
    def of(cls, root: Path, binding: str) -> Fired:
        """What the binding's Triggers table finds in the tree, and the events it declares."""
        kinds, _ = triggers.parse(binding)
        return cls(triggers.instances(root, None, kinds), application_types.life_events(binding))

    def part(self, part: str) -> tuple[bool | None, str]:
        """Whether one part of a trigger has fired (``None``: it cannot be told), and why."""
        if part.startswith(application_types.LIFE):
            event = part.removeprefix(application_types.LIFE)
            said = "declares" if event in self.events else "does not declare"
            return event in self.events, f"the binding {said} the life event '{event}'"
        kind = part.removeprefix(application_types.CODE)
        if kind not in self.found:
            return None, (
                f"the Triggers table has no row for '{kind}', so whether it has fired here is"
                " not known; write that row first"
            )
        count = len(self.found[kind])
        if not count:
            return False, f"'{kind}' has no instance here yet"
        many = "instances" if count > 1 else "instance"
        return True, f"'{kind}' has {count} {many} here: {triggers.listing(self.found[kind])}"


def dropped_row(failure: str, question: str, *, trigger: str, fired: Fired) -> Finding:
    """A Gates row the catalogue gained since the adoption: its template row, and how to fill it.

    Why: ADR 0028. `later:` is for a trigger that has not fired; written for one that has, it is
    green and defers a safeguard already owed. So the item says which this repository is.
    """
    gate = next(
        row.gate for row in project_bite.gate_rows(template_binding()) if row.question == question
    )
    bite = (
        f"binding '{question}' to a tool needs a [[bite]] for gate '{gate}' in"
        f" {project_bite.REGISTRY}"
    )
    row = template_gate_row(question)
    start = (row,)
    always = trigger == application_types.ALWAYS
    said = [] if always else [fired.part(part) for part in application_types.parts(trigger)]
    shown = tuple(why for _, why in said)
    edit = None
    if always:
        fill = "add the template's row and bind it: the question is asked from the first commit"
        fix = Agent(start, (bite,))
        if shipped(row):
            edit = (f"add the '{question}' row, bound to the plugin's own gate", row)
    elif {state for state, _ in said} == {True}:
        fill = f"add the template's row and bind it: its trigger `{trigger}` has fired here"
        fix = Agent(start, (*shown, bite))
    else:
        fill = (
            f"add the template's row, bound, or `later: {trigger}` only while that trigger has"
            " not fired"
        )
        waits = f"; a row that says `later: {trigger}` needs none while that trigger has not fired"
        fix = Agent(start, (*shown, bite + waits))
        # Why: `later:` is written only for a trigger known not to have fired. One whose kind
        # the Triggers table cannot count might have, and `later:` would defer what is owed.
        if None not in {state for state, _ in said}:
            edit = (
                f"add the '{question}' row as `later: {trigger}`",
                with_tool(row, f"later: {trigger}"),
            )
    # Why: what to write changes as the trigger fires; the row owed does not (ADR 0037).
    problem = f"{failure}; {fill}"
    if edit is None:
        return Finding(BINDING_PATH, problem, fix, identity=failure, stops=question)
    does, written = edit
    want = markdown_text.with_row("## Gates", written, template_questions())
    return Finding(BINDING_PATH, problem, Edit(does, want, fix), identity=failure, stops=question)


def shipped(row: str) -> bool:
    """Whether a Gates row's Tool cell is the plugin's own script, which needs no bite."""
    return project_bite.SHIPPED.fullmatch(row.split("|")[2].strip()) is not None


def with_tool(row: str, tool: str) -> str:
    """A Gates row with its Tool cell replaced."""
    cells = row.split("|")
    cells[2] = f" {tool} "
    return "|".join(cells)


def template_questions() -> tuple[str, ...]:
    """The questions of the template binding's Gates table, in its order."""
    return tuple(row.question for row in project_bite.gate_rows(template_binding()))


def triggers_follow(catalogue: application_types.Catalogue) -> tuple[str, ...]:
    """What naming the types asks next: the Triggers kinds each type is asked about (ADR 0028)."""
    return tuple(
        f"`{name}` asks the Triggers table for the kinds {', '.join(sorted(kinds))}"
        for name in catalogue.types
        if (kinds := catalogue.kinds_required({name}))
    )


def life_events_question(root: Path) -> Owner:
    """What to ask the owner of the missing Life events line: the events, the signs, the rows.

    Why: a missing line fails closed where it switches protection off (the merge judge reads it
    as every event, ADR 0028), so leaving it unwritten loosens nothing and is the default.
    """
    catalogue = application_types.load()
    events = catalogue.events
    named = "; ".join(f"`{name}`, when {event.meaning}" for name, event in events.items())
    ask = (
        f"Which of these has already happened to this project: {named}? Write them on the"
        " '- Life events:' line, or `none` for a project that is still young."
    )
    evidence = tuple(
        f"`{path}` matches `{sign}`, a sign of `{name}`"
        for name, signs in application_types.signs_seen(root).items()
        for sign, path in signs.items()
    ) or ("no file in the tree is a sign of a life event",)
    then = tuple(
        f"`{name}` owes the Gates rows {', '.join(owed)}"
        for name in events
        if (owed := catalogue.owed_by(name))
    )
    return Owner(ask, unstated_stage(read(root / BINDING_PATH) or ""), evidence, then)


def unstated_stage(binding: str) -> str:
    """What holds while the Life events line is missing, with the checks that wait for it.

    Why: the merge judge reads a missing line as every event, and a `later:` row waiting on an
    event reads it as none; the owner hears both.
    """
    waiting = [
        row.question
        for row in project_bite.gate_rows(binding)
        if later(row.tool) and application_types.LIFE in row.tool
    ]
    if not waiting:
        return UNSTATED_STAGE
    return (
        f"{UNSTATED_STAGE} The checks such a project needs do not run until you say it is one:"
        f" {', '.join(waiting)}."
    )


def declared_events(root: Path, text: str) -> Iterable[Finding]:
    """The ``- Life events:`` line (ADR 0028): present, and naming only events the catalogue has."""
    match = application_types.EVENTS.search(text)
    if match is None:
        yield Finding(
            BINDING_PATH,
            "has no '- Life events:' line; add it beside '- Application types:'. Without it every"
            " event counts as declared; `none` turns off the owner's review of one-way doors and"
            " unflagged surface and the critic, so a project that is live writes its events"
            " instead (ADR 0028)",
            life_events_question(root),
        )
        return
    known = application_types.load().events
    for name in sorted(application_types.life_events(text)):
        if name not in known:
            yield agent(
                f"{BINDING_PATH}: '- Life events:' names '{name}'; the events are"
                f" {', '.join(known)}"
            )


def check_triggers(root: Path) -> Iterable[Finding]:
    """The Life events line and the Triggers table: how a repository says a trigger has fired.

    Why: ADR 0028. The ``triggers`` gate counts instances with this table, so every kind a question
    asked of the repository's types names must have a row, or the trigger could never fire.
    """
    text = read(root / BINDING_PATH)
    if text is None:
        return
    yield from declared_events(root, text)
    catalogue = application_types.load()
    if not markdown_text.has_section(text, triggers.SECTION.strip()):
        problem = (
            "has no '## Triggers' section; add the template's, one row per kind of code a question"
            " asked of these types names (ADR 0028)"
        )
        # Why: ADR 0037. The section, with the rows every stack writes alike, lets the triggers
        # gate count those kinds from the next change; a kind found by this stack's own paths is
        # an agent's row, which conformance asks for once the types are named. The table goes in
        # padded: every Markdown formatter pads one this narrow, so a project that checks its
        # format would be red on it otherwise.
        rows = "\n".join(MECHANICAL_TRIGGERS.values())
        made = markdown_text.padded(
            template_binding().replace(TRIGGERS_PLACEHOLDER_ROW, rows), triggers.SECTION.strip()
        )
        yield missing_section(BINDING_PATH, problem, "## Triggers", template_text=made)
        return
    kinds, problems = triggers.parse(text)
    for problem in problems:
        yield agent(f"{BINDING_PATH}: {problem}")
    for name in kinds:
        if name not in catalogue.kinds:
            yield agent(
                f"{BINDING_PATH}: the Triggers table names kind '{name}', which the catalogue"
                f" lacks; the kinds are {', '.join(catalogue.kinds)}"
            )
    types = set(application_types.declared(text) or [])
    asked = catalogue.kinds_asked(types)
    for name in sorted(catalogue.kinds_required(types) - set(kinds)):
        why = (
            "which a question asked of these types is triggered by"
            if name in asked
            else "whose arrival reopens the architecture (ADR 0052)"
        )
        yield agent(
            f"{BINDING_PATH}: the Triggers table has no row for kind '{name}', {why}"
            f" ({catalogue.kinds[name]}); write its Paths, or `none: <reason>`"
        )


def later(tool: str) -> bool:
    """Whether a Tool cell says its trigger has not fired (ADR 0028)."""
    return application_types.LATER.match(tool) is not None


DEPENDENCY_STOPS = (
    "the dependency-only judgement of guard-diff and the merge judge, which sends every"
    " dependency change to the owner until the section is filled"
)
DEPENDENCY_RECIPE = "dependencies"


def check_dependencies(root: Path) -> Iterable[Finding]:
    """The Dependencies section: which files hold a stack's dependencies and how to read them.

    Why: ADR 0044. The plugin reads no manifest or lockfile format, so the binding names the files
    and the entry point that prints their records, and a section that cannot be read leaves every
    dependency change to the owner, the one thing the judgement can never be wrong about.
    """
    text = read(root / BINDING_PATH)
    if text is None:
        return
    contract = dependencies.parse(text)
    if contract is None:
        problem = (
            f"has no '{dependencies.SECTION}' section; add the template's, naming the manifests"
            " and lockfiles of this stack and the entry point that reads them (ADR 0044)"
        )
        found = missing_section(
            BINDING_PATH, problem, dependencies.SECTION, template_text=template_binding()
        )
        yield replace(found, stops=DEPENDENCY_STOPS)
        return
    for problem in contract.problems:
        yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=DEPENDENCY_STOPS)
    yield from unguarded_dependency_files(root, contract)
    justfile = read_justfile(root)
    if justfile is None or not contract.entry:
        return
    if not justfile_text.defines_recipe(justfile, DEPENDENCY_RECIPE):
        yield replace(
            missing_recipe(justfile, DEPENDENCY_RECIPE, "dependencies"), stops=DEPENDENCY_STOPS
        )
        return
    block = next(
        b for b in justfile_text.blocks(justfile.split("\n")) if b.name == DEPENDENCY_RECIPE
    )
    body = justfile_text.code_of("\n".join(justfile_text.definition(justfile, DEPENDENCY_RECIPE)))
    problem = ""
    if block.parsed is None or not block.parsed.params:
        problem = "the `dependencies` recipe takes no directory, which the entry point is given"
    elif justfile_text.BINDS in body:
        problem = "the `dependencies` recipe still holds the template's `bind:` placeholder"
    if problem:
        yield replace(agent(f"justfile: {problem}"), stops=DEPENDENCY_STOPS)


def unguarded_dependency_files(root: Path, contract: dependencies.Contract) -> Iterable[Finding]:
    """Each declared file that no guard group covers, tracked or new and not ignored.

    Why: ADR 0044. The method names no lockfile, so the guard on one is whatever the adopting
    agent wrote in ``guards/manifest.toml``. A row that declares a file the manifest misses would
    otherwise drop the guard silently: a change to it would merge without the owner.
    """
    text = read(root / "guards/manifest.toml")
    if text is None:
        return
    try:
        guards = guard_manifest.parse(text)
    except guard_manifest.ManifestError:
        return  # the manifest check reports it
    listed = gatelib.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    tracked = (
        listed.split("\0")
        if listed is not None
        else [
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and not any(x.startswith(".") for x in p.relative_to(root).parts)
        ]
    )
    for path in sorted(p for p in tracked if p and contract.recognised(p)):
        if guards.group_of(path) is None:
            yield agent(
                f"guards/manifest.toml: the Dependencies section declares {path}, which no group"
                " guards; add it to a group, or a change to it merges without the owner"
            )


def bound(tool: str) -> bool:
    """Whether a Tool cell names a tool: it is not empty, ``later:`` or ``none``."""
    return bool(tool) and not later(tool) and application_types.NONE.match(tool) is None


SUPPRESSIONS_STOPS = (
    "the suppressions gate and the coverage gate's search for exclusions on absolute paths,"
    " which count nothing until the section is filled"
)


def check_suppressions(root: Path) -> Iterable[Finding]:
    """The Suppressions section: the directives of the tools this repository bound (ADR 0042).

    Why: the plugin names no directive of any tool, so the gate counts what the binding lists. A
    bound row whose tool a directive can silence must say which kinds do, or that none does, so
    a tool bound with no kind listed cannot go uncounted without a deterministic gate saying so.
    """
    text = read(root / BINDING_PATH)
    if text is None:
        return
    spec = suppressions.parse(text)
    if spec is None:
        problem = (
            f"has no '{suppressions.SECTION}' section; add the template's, one row per kind of"
            " directive the bound tools read (ADR 0042)"
        )
        found = missing_section(
            BINDING_PATH, problem, suppressions.SECTION, template_text=template_binding()
        )
        yield replace(found, stops=SUPPRESSIONS_STOPS)
        return
    for problem in spec.problems:
        yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=SUPPRESSIONS_STOPS)
    directed = application_types.load().directed
    for row in project_bite.gate_rows(text):
        if row.question in directed and bound(row.tool) and row.question not in spec.silenced:
            problem = (
                f"gate '{row.question}' is bound to a tool, and the Suppressions section does"
                " not say which kinds of directive silence it: add its row, naming kinds in"
                " backticks or `none: <reason>`"
            )
            yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=SUPPRESSIONS_STOPS)


SIGNS_STOPS = "the route's proposal of a life event it sees a sign of, which waits for the table"
PINS_STOPS = "the pins gate's reading of the project's image files, which reads none until filled"
MUTATION_STOPS = "the mutation gate, which reads no tool's own report any more (ADR 0050)"
MUTATION_SCRIPT = "mutation_survivors.py"
MUTATION_INPUTS = ("--mutants", "--changed")


def check_signs(root: Path) -> Iterable[Finding]:
    """The Life event signs table: which files of this stack mark a life event (ADR 0050).

    Why: the plugin names no host and no store, so the route can propose an event only from the
    files this binding lists. A missing row proposes nothing, which fails closed: the event is
    never declared for the owner, and the row is owed, not red, on an adopted repository.
    """
    text = read(root / BINDING_PATH)
    if text is None:
        return
    table = application_types.signs_in(text)
    if table is None:
        problem = (
            f"has no '{application_types.SIGNS_SECTION}' section; add the template's, one row per"
            " life event whose signs depend on the stack (ADR 0050)"
        )
        found = missing_section(
            BINDING_PATH,
            problem,
            application_types.SIGNS_SECTION,
            template_text=template_binding(),
        )
        yield replace(found, stops=SIGNS_STOPS)
        return
    for problem in table.problems:
        yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=SIGNS_STOPS)


def check_pins(root: Path) -> Iterable[Finding]:
    """The Image pins section: the files that name an image and how (ADR 0050)."""
    text = read(root / BINDING_PATH)
    if text is None:
        return
    rules, problems = pins.parse(text)
    if rules is None and problems and problems[0] == f"has no '{pins.SECTION}' section":
        problem = (
            f"{problems[0]}; add the template's, naming the files that name a container image"
            " and the pattern that finds the reference in each (ADR 0050)"
        )
        found = missing_section(
            BINDING_PATH, problem, pins.SECTION, template_text=template_binding()
        )
        yield replace(found, stops=PINS_STOPS)
        return
    for problem in problems:
        yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=PINS_STOPS)


def check_mutation(root: Path) -> Iterable[Finding]:
    """A recipe that hands the mutation gate a tool's own report: the gate reads one format.

    Why: ADR 0050. The plugin reads one JSON line per mutant and no tool's report, so a recipe
    written for an earlier version, which passed a report or a results file, is red until it
    converts the report itself and the register's entries are written again from the new records.
    """
    for path in justfile_paths(root):
        for line in (read(path) or "").replace("\\\n", " ").splitlines():
            if line.lstrip().startswith("#"):
                continue
            if MUTATION_SCRIPT in line and not any(flag in line for flag in MUTATION_INPUTS):
                name = path.relative_to(root).as_posix()
                problem = (
                    f"a recipe runs {MUTATION_SCRIPT} on a tool's own report; it takes"
                    " `--tool <name> --mutants <file>`, one JSON line per mutant, which the"
                    " recipe writes from the report, and the register's entries are written"
                    " again for the new identities (ADR 0050)"
                )
                yield replace(agent(f"{name}: {problem}"), stops=MUTATION_STOPS)
                return


def check_operability(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH) or ""
    asked = set(FIRST_CELL.findall(markdown_text.section(text, "## Gates")))
    types_check_asks = application_types.load().questions
    for question in OPERABILITY_QUESTIONS:
        if text and question not in asked and question not in types_check_asks:
            problem = f"the Gates table has no '{question}' row"
            row = template_gate_row(question)
            fix: Edit | Agent = Agent()
            if shipped(row):
                want = markdown_text.with_row("## Gates", row, template_questions())
                does = f"add the '{question}' row, bound to the plugin's own gate"
                fix = Edit(does, want, Agent(start=(row,)))
            yield Finding(BINDING_PATH, problem, fix, stops=question)
    waiting = {row.question for row in project_bite.gate_rows(text) if later(row.tool)}
    for heading, questions in OPERABILITY_SECTIONS.items():
        if text and not waiting >= set(questions) and markdown_text.empty_body(text, heading):
            yield agent(f"{BINDING_PATH}: has no '{heading}' section, or it is empty")
    if not (root / FLAG_REGISTRY).is_file():
        yield whole_file(str(FLAG_REGISTRY), "missing; an empty file declares no flags")


def pr_template_path(root: Path) -> str:
    text = read(root / BINDING_PATH) or ""
    match = PR_TEMPLATE_LINE.search(text)
    return match.group(1) if match else DEFAULT_PR_TEMPLATE


def check_speed(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH) or ""
    if text and not markdown_text.has_section(text, SPEED_SECTION):
        yield agent(f"{BINDING_PATH}: has no '{SPEED_SECTION}' section (ceilings, shards)")
    elif text:
        yield from check_speed_parts(markdown_text.section(text, SPEED_SECTION))
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
                problem = f"nothing {meaning} ('{marker}' absent)"
                does = (
                    "pass the template's budget flags to each budget call an older template wrote"
                )
                edit = Edit(does, budget_flags(name), Agent())
                yield Finding(str(path.relative_to(root)), problem, edit)
        if path.name in TIER_WORKFLOWS and SHARD_MATRIX not in content:
            yield agent(
                f"{path.relative_to(root)}: no long gate runs over the binding's shard count "
                f"('{SHARD_MATRIX}' absent)"
            )


def budget_flags(workflow: str) -> gatelib.Want:
    """The want of a tier workflow whose budget calls carry the current template's flags.

    A call as an older template wrote it gains ``--tier``, and in ``ci.yml`` ``--over`` and the
    variable it reads. Why: ADR 0025. A line the project wrote itself is left as it is, so the
    check stays red on it.
    """
    tier = "tier2" if workflow == "deep.yml" else "tier1"
    over = ' --over "$OVER"' if workflow == "ci.yml" else ""
    flags = {OLD_TIER0_BUDGET: over, OLD_GATE_BUDGET: f" --tier {tier}{over}"}
    template = template_file(f".github/workflows/{workflow}").splitlines()
    variable = [line for line in template if line.startswith("  OVER:")]
    said = (
        template[template.index(variable[0]) - 1 : template.index(variable[0]) + 1]
        if variable
        else []
    )

    def want(current: str | None) -> str:
        lines = (current or "").split("\n")
        for index, line in enumerate(lines):
            indent, _, run = line.partition("- run: ")
            if not indent.strip() and run in flags and flags[run]:
                lines[index] = (
                    f"{indent}- run: {run.replace(' --report ', f'{flags[run]} --report ', 1)}"
                )
        if over and said and not any(line.startswith("  OVER:") for line in lines):
            at = next((i for i, line in enumerate(lines) if line.startswith(PLUGIN_ENV)), None)
            if at is not None:
                lines[at + 1 : at + 1] = said
        return "\n".join(lines)

    return want


def with_shards(text: str | None) -> str:
    """The binding with ``- Shards: 1``, the template's day-one count, ending its Speed list."""
    lines = (text or "").split("\n")
    if SPEED_SECTION not in lines or any(line.startswith("- Shards:") for line in lines):
        return text or ""
    start = lines.index(SPEED_SECTION)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    listed = [i for i in range(start + 1, end) if lines[i].startswith("- ")]
    at = listed[-1] + 1 if listed else start + 1
    lines[at:at] = ["- Shards: 1"] if listed else ["", "- Shards: 1"]
    return "\n".join(lines)


def check_speed_parts(body: str) -> Iterable[Finding]:
    stated = dict(SPEED_PART.findall(body))
    for part in SPEED_PARTS:
        value = stated.get(part, "")
        if part == "Shards" and not SHARD_COUNT.fullmatch(value):
            problem = (
                "the Speed section needs '- Shards: N', a whole number alone on its line, 1 on day"
                " one; CI reads it"
            )
            edit = Edit("state the template's day-one shard count, 1", with_shards, Agent())
            yield Finding(BINDING_PATH, problem, edit)
        elif not value or value.startswith("<"):
            yield agent(f"{BINDING_PATH}: the Speed section does not say how '{part}' is met")


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
    bound = [group for group in groups if isinstance(group.get("paths"), list)]
    guarded = {pattern for group in bound for pattern in group["paths"]}
    locked = {
        pattern for group in bound if group.get("edit") == "deny" for pattern in group["paths"]
    }
    for pattern in sorted(LOCKED_PATTERNS - locked):
        yield agent(f"guards/manifest.toml: '{pattern}' is not in an edit = \"deny\" group")
    for pattern in sorted(GUARDED_PATTERNS - guarded):
        yield agent(f"guards/manifest.toml: '{pattern}' is in no group")


def check_default_branch(root: Path) -> Iterable[Finding]:
    """ADR 0041: the default branch is found from refs, not from the main work tree's reflog.

    A repository with no remote and no ``main`` or ``master`` has its default branch read from
    the reflog, which git expires; until the owner names the branch one of those ways, the guards
    are as strong as that log.
    """
    if committed.anchored_on_reflog(root):
        yield owner(
            "guards/manifest.toml: the default branch is found only through the main work tree's"
            " history of branch switches, which git forgets after about three months",
            "Add the forge remote (then fetch it and set its head), or name the default branch"
            " `main` or `master`? Either is a one-time step, and the guards then read the branch"
            " from git's own refs.",
            assumed="the guards keep reading the branch the main work tree was first on",
        )


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
    for kind, meaning in DOOR_MEANINGS.items():
        if kind not in kinds:
            yield owner(
                f"guards/manifest.toml: no [[door]] with on = '{kind}'; the merge judge reads"
                " the one-way doors from it",
                f"Does this repository have a door of kind '{kind}', {meaning}? Add a"
                f" [[door]] with on = '{kind}' and its paths, or `none = \"<the reason>\"`.",
                assumed=NO_DOOR,
            )
    for door in doors:
        name = door.get("name", "<unnamed>")
        if len(str(door.get("reason", ""))) < MIN_REASON:
            yield agent(
                f"guards/manifest.toml: door '{name}' needs a reason of {MIN_REASON}+ characters"
            )
        if not door.get("paths") and len(str(door.get("none", ""))) < MIN_REASON:
            yield owner(
                f"guards/manifest.toml: door '{name}' has no paths yet; bind it, or say why the"
                ' repository has none with `none = "<reason>"`',
                f"Which paths shut the door '{name}' ({door.get('reason', 'no reason given')})?"
                ' List them, or say why the repository has none with `none = "<the reason>"`.',
                assumed=NO_DOOR,
            )


def forge_workflow(
    root: Path, name: str, *, unmet: str, shape: tuple[tuple[str, str], ...]
) -> Iterable[Finding]:
    """A workflow that a forge running ``ci`` must also have, and the words that show its shape."""
    if not (root / WORKFLOWS / "ci.yml").is_file():
        return
    text = read(root / WORKFLOWS / name)
    if text is None:
        yield whole_file(str(WORKFLOWS / name), f"missing; {unmet}")
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
    binding = read(root / BINDING_PATH)
    if binding is None:
        return
    pin = plugin_pin.pinned(binding)
    if pin is None:
        line = plugin_pin.line_for(plugin_pin.PLUGIN)
        problem = (
            "no '- Ratchet plugin: <version> at <commit>' line; add under '## Runner', for the"
            f" plugin in use: {line}"
        )
        edit = Edit(
            "add the pin line for the plugin in use", plugin_pin.with_pin(line), Agent((line,))
        )
        yield Finding(BINDING_PATH, problem, edit)
    yield from check_pin_recipe(root)
    if pin is None:
        return
    for name in (*TIER_WORKFLOWS, MERGE_WORKFLOW, WEEKLY_WORKFLOW):
        text = read(root / WORKFLOWS / name) or ""
        refs = plugin_pin.refs(text)
        if not refs and "RATCHET_PLUGIN" in text:
            yield agent(
                f"{WORKFLOWS / name}: checks no plugin out by `repository:` then `ref:`, so "
                "nothing holds it to the commit the binding pins"
            )
        moved = Edit(
            f"set the plugin `ref` to the pinned {pin.commit[:12]}",
            plugin_pin.with_refs(pin.commit),
            Agent(),
        )
        for ref in sorted(refs - {pin.commit}):
            problem = (
                f"checks the plugin out at '{ref}', not the commit the binding pins ({pin.commit})"
            )
            yield Finding(str(WORKFLOWS / name), problem, moved)


def check_toolchain(root: Path) -> Iterable[Finding]:
    """A workflow whose toolchain setup is still the template's placeholder."""
    for path in sorted((root / WORKFLOWS).glob("*.yml")):
        template = read(ADOPT_TEMPLATES / ".github/workflows" / path.name)
        if template is None:
            continue
        unbound = set(after_marker(template.splitlines()).values())
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, following in after_marker(lines).items():
            if following in unbound:
                # Why: the line moves when one is added above it, the job does not, so an owed
                # entry names the job and excuses no placeholder in another (ADR 0037).
                unset = (
                    f"in job `{job_of(lines, number)}` still says '{TOOLCHAIN_MARKER}' and the"
                    " next line is the template's, so no step sets up the project's toolchain"
                )
                yield Finding(
                    str(path.relative_to(root)),
                    f"line {number} {unset}; add the steps that install it, pinned by commit SHA",
                    Agent(),
                    identity=unset,
                    stops=WEEKLY_CHECKS if path.name == WEEKLY_WORKFLOW else "",
                )


def job_of(lines: list[str], number: int) -> str:
    """The id of the workflow job that holds line ``number`` (counted from 1), or ``?``."""
    for line in reversed(lines[: number - 1]):
        if line.startswith("jobs:"):
            break
        if match := WORKFLOW_JOB.match(line):
            return match.group(1)
    return "?"


def after_marker(lines: list[str]) -> dict[int, str]:
    """Each line (counted from 1) that holds the toolchain marker, with the line after it."""
    return {
        number: lines[number]
        for number, line in enumerate(lines, start=1)
        if TOOLCHAIN_MARKER in line and number < len(lines)
    }


def tier_recipes(binding: str, justfile: str) -> dict[str, str]:
    """The recipe each tier runs, by entry point, for every tier the justfile defines.

    The tiers are those the template runs `plugin-pin` first in. Each is named as the binding's
    Entry points row names it, or as the template's row does while the binding has none.
    """
    commands = entry_points(binding)
    rows = template_rows()
    named = {}
    for tier in justfile_text.first_dependents(template_file("justfile"), PIN_RECIPE):
        recipe = RUNS_JUST.match((commands.get(tier) or rows[tier][1]).strip("`"))
        if recipe is not None and justfile_text.defines_recipe(justfile, recipe.group(1)):
            named[tier] = recipe.group(1)
    return named


def check_pin_recipe(root: Path) -> Iterable[Finding]:
    """Every tier runs plugin_pin.py, so no local run of one uses a plugin other than the pin.

    Why: a recipe that exists but that no tier runs checks nothing. A tier runs the pin check when
    its recipe, or a recipe it depends on or calls with ``just``, does. A justfile with no tier
    recipe at all is held to naming the script.
    """
    justfile = read_justfile(root)
    if justfile is None:
        return
    tiers = tier_recipes(read(root / BINDING_PATH) or "", justfile)
    unpinned = {
        tier: recipe
        for tier, recipe in tiers.items()
        if not justfile_text.runs(justfile, recipe, PIN_SCRIPT)
    }
    if not unpinned and (tiers or PIN_SCRIPT in justfile_text.code_of(justfile)):
        return
    text = template_file("justfile")
    problem = (
        f"not every tier runs {PIN_SCRIPT}, so a local run can use a plugin other than the one"
        " the binding pins; copy `plugin-pin` from the template justfile, as the first"
        " dependency of each tier"
    )
    start = template_definition(PIN_RECIPE)
    unrun = tuple(
        f"`{recipe}`, the binding's {tier}, does not run it" for tier, recipe in unpinned.items()
    )
    defined = justfile_text.defines_recipe(justfile, PIN_RECIPE)
    reasons = () if defined else justfile_text.unmet(text, justfile, PIN_RECIPE)
    if reasons:
        yield Finding("justfile", problem, Agent(start=start, then=reasons + unrun))
        return
    copied = [] if defined else [justfile_text.with_recipe(text, PIN_RECIPE)]
    wants = [
        justfile_text.with_first_dependency(recipe, PIN_RECIPE) for recipe in unpinned.values()
    ]
    does = f"run `{PIN_RECIPE}` first in each tier"
    edit = Edit(
        does if defined else f"copy the template's `{PIN_RECIPE}` recipe and {does}",
        gatelib.chained(*copied, *wants),
        Agent(start=start, then=unrun),
    )
    yield Finding("justfile", problem, edit)


INSTALL_STOPS = "the check that a fresh checkout has the tools the recipes run"
INSTALL_BULLET = re.compile("^- (?:" + "|".join(installs.LABELS) + "):")


def install_section(bullets: dict[str, str]) -> str:
    """The template's Install section with its bullets written as ``bullets`` says."""
    block = installs.SECTION + markdown_text.section(template_binding(), installs.SECTION).rstrip()
    lines = block.splitlines()
    at = next(i for i, line in enumerate(lines) if INSTALL_BULLET.match(line))
    kept = [line for line in lines if not INSTALL_BULLET.match(line)]
    written = [f"- {label}: {value}" for label, value in bullets.items()]
    return "\n".join([*kept[:at], *written, *kept[at:]])


def missing_install_section(root: Path, text: str, justfile: str | None) -> Finding:
    """The Install section a binding lacks: what its justfile states, else the template's."""
    problem = (
        f"has no '{installs.SECTION}' section, which says whether a fresh checkout installs the"
        " tools before a recipe runs one (ADR 0043)"
    )
    found = (
        installs.derived(
            justfile,
            tracked=lambda name: (root / name).is_file(),
            tiers=tier_recipes(text, justfile).values(),
        )
        if justfile is not None
        else None
    )
    if found is None:
        return missing_section(
            BINDING_PATH, problem, installs.SECTION, template_text=template_binding()
        )
    want = markdown_text.with_section(
        installs.SECTION, install_section(found), headings(template_binding())
    )
    return Finding(
        BINDING_PATH,
        problem,
        Edit(
            f"write the '{installs.SECTION}' section from the justfile's own install recipe",
            want,
            Agent((installs.SECTION, *install_section(found).splitlines()[1:])),
        ),
    )


def check_installs(root: Path) -> Iterable[Finding]:
    """What the binding says about an install first, held to the justfile (ADR 0043).

    Why: ADR 0039. ``merge.py`` runs the gates in a new work tree and CI runs each in a new
    checkout. The binding says whether this stack's tools need an install first; when they do,
    every recipe that runs one depends on the ``install`` recipe, which names the lockfile it is
    frozen to, and every tier runs it once before its gates start so no two install at once.
    """
    text = read(root / BINDING_PATH)
    if text is None:
        return
    justfile = read_justfile(root)
    spec = installs.parse(text)
    if spec is None:
        yield replace(missing_install_section(root, text, justfile), stops=INSTALL_STOPS)
        return
    for problem in spec.problems:
        yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=INSTALL_STOPS)
    if justfile is None or not spec.needed or spec.problems:
        return
    yield from installing(root, text, justfile, spec=spec)


def installing(root: Path, text: str, justfile: str, *, spec: installs.Spec) -> Iterable[Finding]:
    """The justfile against a binding that says an install comes first."""
    lockfile = spec.lockfile
    if not (root / lockfile).is_file():
        yield agent(f"{BINDING_PATH}: the Install section's lockfile '{lockfile}' is not a file")
    missing = [name for name in spec.recipes if not justfile_text.defines_recipe(justfile, name)]
    if missing:
        yield agent(
            f"{BINDING_PATH}: the Install section lists the recipes {_listed(missing)}, which the"
            " justfile does not define"
        )
    tiers = tier_recipes(text, justfile).values()
    bare = [
        name
        for name in spec.recipes
        if name not in missing and not installs.reaches_install(justfile, name)
    ]
    unrun = [name for name in tiers if not installs.depends_directly(justfile, name)]
    frozen = installs.installs_from_lock(justfile, lockfile)
    if not (bare or unrun or not frozen):
        return
    reasons = []
    if not frozen:
        reasons.append(f"the justfile's `install` recipe does not name {lockfile}")
    if bare:
        reasons.append(f"the recipes {_listed(bare)} do not depend on `install`")
    if unrun:
        reasons.append(f"the recipes {_listed(unrun)} do not run `install` before their gates")
    # Why: the problem names every recipe the binding lists, fixed or not, so an edit that mends
    # some of them leaves it as it was and the trial does not count the edit as the fix.
    problem = (
        f"the recipes {_listed(list(spec.recipes))} run tools that a fresh checkout does not have,"
        f" so they fail in the merge's work tree and in CI; each must depend on an `install`"
        f" recipe that installs from {lockfile} and does nothing once it has, and every tier must"
        " run it before its gates start"
    )
    wants = [
        *(justfile_text.with_first_dependency(name, installs.INSTALL) for name in bare),
        *(
            justfile_text.with_dependency_after(name, installs.INSTALL, PIN_RECIPE)
            for name in unrun
        ),
    ]
    edit = Edit(
        "make every recipe that runs the installed tools, and every tier, depend on `install`",
        gatelib.chained(*wants),
        Agent(then=tuple(reasons)),
    )
    yield Finding(
        "justfile",
        problem,
        edit,
        identity="the recipes that run installed tools run with no install ahead of them",
    )


def _listed(names: Sequence[str]) -> str:
    """Recipe names as code, for a sentence."""
    return ", ".join(f"`{name}`" for name in names)


def answered_gates(binding: str) -> set[str]:
    """The entry points of the Gates rows the binding answers with something other than none."""
    return {
        row.gate
        for row in project_bite.gate_rows(binding)
        if row.tool and not application_types.NONE.match(row.tool) and not later(row.tool)
    }


def check_tier_lists(root: Path, justfile: str) -> Iterable[Finding]:
    """A tier list the tier workflows read, which the justfile does not define.

    Why: ADR 0037. A tier whose recipe only names its gates gives its list as it stands, and a
    list of the gates to shard starts empty, as the template's day one does; the upgrade writes
    both. A tier that runs its gates some other way is an agent's to list.
    """
    tiers = tier_recipes(read(root / BINDING_PATH) or "", justfile)
    for workflow, lists in TIER_LISTS.items():
        if not (root / WORKFLOWS / workflow).is_file():
            continue
        for name, meaning in lists:
            if justfile_text.defines_variable(justfile, name):
                continue
            problem = (
                f"has no `{name}` variable, which the template's `shape` job of `{workflow}`"
                f" reads with `just --evaluate {name}`; add the template's, naming {meaning}"
            )
            fix = Agent(start=template_definition(name))
            tier = name.split("_")[0]
            if name.endswith("_sharded"):
                does = f"add `{name}`, naming no gate to shard"
                edit = Edit(does, justfile_text.with_variable(name, lambda _: '""'), fix)
                yield Finding("justfile", problem, edit)
            elif (recipe := tiers.get(tier)) and justfile_text.dependencies(justfile, recipe):
                does = f"add `{name}`, listing the gates the `{recipe}` recipe runs"
                edit = Edit(does, justfile_text.with_variable(name, gates_of(recipe)), fix)
                yield Finding("justfile", problem, edit)
            else:
                yield Finding("justfile", problem, fix)


def gates_of(recipe: str) -> Callable[[str], str]:
    """The value of a tier list: the gates a recipe with no body depends on, quoted."""

    def value(justfile: str) -> str:
        listed = justfile_text.dependencies(justfile, recipe) or ()
        return '"' + " ".join(name for name in listed if name != PIN_RECIPE) + '"'

    return value


def unrun_gates(binding: str, justfile: str) -> Iterable[Finding]:
    """A gate the plugin ships, bound at tier 0 or 1, that its tier's recipe never reaches.

    Why: ADR 0037. A row the upgrade binds names a gate; with no recipe, or no tier that runs it,
    conformance would be green on a safeguard that is off. A tier whose recipe only names its
    gates gets the gate, and the template's recipe when the justfile lacks it.
    """
    tiers = tier_recipes(binding, justfile)
    template = template_file("justfile")
    for row in project_bite.gate_rows(binding):
        tier = f"tier{row.tier}"
        if not project_bite.SHIPPED.fullmatch(row.tool) or tier not in ("tier0", "tier1"):
            continue
        recipe = tiers.get(tier)
        if recipe is None or justfile_text.undefined(justfile, recipe):
            continue
        if row.gate in justfile_text.reaches(justfile, recipe):
            continue
        tree = tiers.get("tier0-tree")
        target = (
            tree if tier == "tier0" and tree in justfile_text.reaches(justfile, recipe) else recipe
        )
        problem = (
            f"no tier runs `{row.gate}`, which the binding binds to '{row.question}' at tier"
            f" {row.tier}, so that gate never runs; run it in `{target}`"
        )
        defined = justfile_text.defines_recipe(justfile, row.gate)
        reasons = () if defined else justfile_text.unmet(template, justfile, row.gate)
        fix = Agent(start=template_definition(row.gate), then=reasons)
        if reasons or justfile_text.dependencies(justfile, target) is None:
            yield Finding("justfile", problem, fix, stops=row.question)
            continue
        copied = [] if defined else [justfile_text.with_recipe(template, row.gate)]
        wants = [*copied, justfile_text.with_dependency(target, row.gate)]
        if target == recipe:
            # Why: a tier list the upgrade wrote from this tier in an earlier pass lists it too.
            wants.append(justfile_text.with_listed(f"{tier}_gates", row.gate))
        want = gatelib.chained(*wants)
        does = f"run `{row.gate}` in `{target}`" + (
            "" if defined else ", from the template's recipe"
        )
        yield Finding("justfile", problem, Edit(does, want, fix), stops=row.question)


@dataclass(frozen=True)
class Matrix:
    """A job's gates as its workflow lists them: the lines that hold the list, and the gates."""

    start: int
    end: int
    gates: tuple[str, ...]


def static_matrix(lines: list[str], job: str) -> Matrix | None:
    """The gates a job's ``gate:`` matrix lists by name, on one line or one per line.

    ``None`` when the job has no such list, such as one that reads its gates from the justfile.
    """
    if f"  {job}:" not in lines:
        return None
    start = lines.index(f"  {job}:")
    end = next((i for i in range(start + 1, len(lines)) if TOP_KEY.match(lines[i])), len(lines))
    at = next((i for i in range(start, end) if MATRIX_GATE.match(lines[i])), None)
    if at is None:
        return None
    rest = lines[at].partition("gate:")[2].strip()
    if rest.startswith("[") and rest.endswith("]"):
        items = [item.strip() for item in rest[1:-1].split(",") if item.strip()]
        return Matrix(at, at, tuple(items)) if all(map(GATE_NAME.fullmatch, items)) else None
    if rest or at + 1 >= end or lines[at + 1].strip() != "[":
        return None
    close = next((i for i in range(at + 2, end) if lines[i].strip() == "]"), None)
    if close is None:
        return None
    items = [line.strip().removesuffix(",") for line in lines[at + 2 : close]]
    return Matrix(at + 1, close, tuple(items)) if all(map(GATE_NAME.fullmatch, items)) else None


def with_matrix_gate(job: str, gate: str) -> gatelib.Want:
    """The want of a workflow whose job's listed matrix also names ``gate``, laid out as it is."""

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        matrix = static_matrix(lines, job)
        if matrix is None or gate in matrix.gates:
            return text
        if matrix.start == matrix.end:
            line = lines[matrix.start]
            close = line.rindex("]")
            joined = ", " if matrix.gates else ""
            lines[matrix.start] = f"{line[:close].rstrip()}{joined}{gate}{line[close:]}"
            return "\n".join(lines)
        last = lines[matrix.end - 1]
        indent = last[: len(last) - len(last.lstrip())] + ("" if matrix.gates else "  ")
        if matrix.gates and not last.rstrip().endswith(","):
            lines[matrix.end - 1] = last.rstrip() + ","
        lines.insert(matrix.end, f"{indent}{gate},")
        return "\n".join(lines)

    return want


def ci_unrun(root: Path, binding: str) -> Iterable[Finding]:
    """A gate the plugin ships, bound at tier 1, that the CI tier 1 job's listed matrix lacks.

    Why: ADR 0037. A workflow an older template wrote names its tier 1 gates in the job; a gate the
    tier's recipe runs and the list lacks runs on the laptop and never in CI.
    """
    path = WORKFLOWS / "ci.yml"
    lines = (read(root / path) or "").split("\n")
    matrix = static_matrix(lines, "tier1")
    if matrix is None:
        return
    for row in project_bite.gate_rows(binding):
        if row.tier != "1" or not project_bite.SHIPPED.fullmatch(row.tool):
            continue
        if row.gate in matrix.gates:
            continue
        problem = (
            f"the tier1 job lists the gates it runs and not `{row.gate}`, which the binding binds"
            f" to '{row.question}' at tier 1, so CI never runs that gate; add it to the list"
        )
        does = f"add `{row.gate}` to the gates the tier1 job lists"
        edit = Edit(does, with_matrix_gate("tier1", row.gate), Agent())
        yield Finding(str(path), problem, edit, stops=row.question)


def asks_hooks(ratchet: str) -> bool:
    """Whether the ``ratchet`` recipe asks if a commit would run tier 0."""
    return "hooks-installed" in ratchet or "hooks_installed.py" in ratchet


def ratchet_asks(justfile: str) -> Finding:
    """The ``ratchet`` recipe that does not ask first: the template's first line is added."""
    text = template_file("justfile")
    start = template_definition("ratchet")
    problem = (
        "the `ratchet` recipe raises the thresholds without asking whether a commit would run"
        " tier 0; make `just hooks-installed` its first line, as the template's is"
    )
    first = next(line for line in start if line[:1].isspace()).strip()
    stands = justfile_text.defines_recipe(justfile, "hooks-installed")
    reasons = () if stands else justfile_text.unmet(text, justfile, "hooks-installed")
    if reasons:
        return Finding("justfile", problem, Agent(start=start, then=reasons))
    copied = [] if stands else [justfile_text.with_recipe(text, "hooks-installed")]
    want = gatelib.chained(*copied, justfile_text.with_first_line("ratchet", first))
    does = "ask `hooks-installed` first in the `ratchet` recipe, as the template does"
    return Finding("justfile", problem, Edit(does, want, Agent(start=start)))


def check_runner(root: Path) -> Iterable[Finding]:
    """What the template justfile's tiers rely on, read only where a justfile is the runner."""
    justfile = read_justfile(root)
    if justfile is None:
        return
    yield from unrun_gates(read(root / BINDING_PATH) or "", justfile)
    yield from ci_unrun(root, read(root / BINDING_PATH) or "")
    yield from check_tier_lists(root, justfile)
    code = justfile_text.code_of(justfile)
    gates = answered_gates(read(root / BINDING_PATH) or "")
    if gates & OVER_THE_CHANGE and "changed.py" not in code:
        yield Finding(
            "justfile",
            "no gate runs through `changed.py`, so a gate over the change has no base on the"
            " adoption; add the template's `changed` variable and run diff-coverage, test-counts"
            " and mutation-changed as `{{changed}} -- <command>`, with `{base}` for the commit"
            " compared with",
            Agent(start=template_definition("changed")),
        )
    ratchet = "\n".join(justfile_text.definition(justfile, "ratchet"))
    if ratchet and not asks_hooks(ratchet):
        yield ratchet_asks(justfile)
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
            f"{BINDING_PATH}: the `- Commit hooks:` line names no command after "
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
    justfile = read_justfile(root)
    if justfile is not None and hooks_installed.PROBE_ENV not in justfile:
        # Why: an owed entry holds this text, so it names no path of the plugin's checkout.
        yield agent(
            f"justfile: the `pre-commit` and `commit-msg` recipes do not answer "
            f"{hooks_installed.PROBE_ENV}, so `hooks_installed.py` cannot tell whether a commit "
            "would run tier 0; copy them from the template justfile",
            start=template_definition("pre-commit") + template_definition("commit-msg"),
        )


def settings_finding(
    found: settings_rules.Drift, manifest: dict[str, object], text: str | None
) -> Finding:
    """One line of settings drift: the plugin regenerates its rules, the project's are asked."""
    if found.fault is settings_rules.Fault.OWN:
        head, _, tail = found.line.rpartition("' in ")
        said = plain_rule(head.split("'", 1)[1], tail.split(" ", 1)[0])
        return owner(
            found.line,
            f"Should Claude keep this rule: {said}? No version of Ratchet writes it, so the"
            " settings check stays red while it is there. Remove it from"
            f" {settings_rules.SETTINGS}, or keep it in your own `.claude/settings.local.json`,"
            " which no gate reads.",
            assumed=(
                "Claude's settings for this project hold a permission rule that no version of"
                f" Ratchet writes: {said}. It stays as it is, and the settings check stays red on"
                " it until you say whether to keep it."
            ),
        )
    if found.fault is settings_rules.Fault.MANIFEST:
        return agent(found.line)
    path, _, problem = found.line.partition(": ")
    settings = settings_rules.parse_settings(text)
    kept = settings_rules.stricter(settings or {}, manifest)
    does = "regenerate the permission rules, keeping the project's own"
    if kept:
        does += f"; keeping in deny what you made stricter: {', '.join(f'`{r}`' for r in kept)}"
    broken = (f"{path} is not a JSON object, so it is left as it is",) if settings is None else ()
    edit = Edit(does, settings_rules.regenerated(manifest), Agent(then=broken))
    return Finding(path, problem, edit)


def plain_rule(rule: str, kind: str) -> str:
    """A permission rule in words: ``Bash(tool:*)`` in ``ask`` is "Claude asks ... runs `tool`"."""
    tool, _, argument = rule.partition("(")
    argument = argument.removesuffix(")").removesuffix(":*").removesuffix(" *")
    asks, denies = RULE_VERBS.get(tool, ("uses", "use")) if argument else ("uses", "use")
    what = f"`{argument}`" if tool in RULE_VERBS and argument else f"`{rule}`"
    if kind == "deny":
        return f"Claude may never {denies} {what}"
    return f"Claude asks you before it {asks} {what}"


def check_settings(root: Path) -> Iterable[Finding]:
    if (root / settings_rules.MANIFEST).is_file():
        manifest = settings_rules.read_manifest(root) or {}
        text = settings_rules.settings_text(root)
        for found in settings_rules.drifts(root):
            yield settings_finding(found, manifest, text)


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
        elif not has_change_ceiling(data):
            problem = (
                "quality/thresholds.toml: no live `[ceilings.change]`, so no pull request is held"
                " to a size"
            )
            edit = Edit(
                "write the method's first change-size ceiling", with_change_ceiling, Agent()
            )
            yield Finding("quality/thresholds.toml", problem, edit)
        elif legacy_change_ceiling(data):
            yield Finding(
                "quality/thresholds.toml",
                "`[ceilings.change]` holds `files` and `lines`, which counted tests; ADR 0045"
                " moved them to `product_files` and `product_lines` and added `test_lines`",
                Edit("move the change ceiling to its product keys", moved_change_ceiling, Agent()),
            )
    if not (root / "quality/snapshots").is_dir():
        yield agent("quality/snapshots: missing")


def has_change_ceiling(data: dict[str, object]) -> bool:
    ceilings = data.get("ceilings")
    return isinstance(ceilings, dict) and isinstance(ceilings.get("change"), dict)


def legacy_change_ceiling(data: dict[str, object]) -> bool:
    """Whether ``[ceilings.change]`` still holds the keys ADR 0045 moved."""
    ceilings = data.get("ceilings")
    table = ceilings.get("change") if isinstance(ceilings, dict) else None
    return isinstance(table, dict) and any(key in table for key in ("files", "lines"))


def moved_change_ceiling(text: str | None) -> str:
    """The thresholds with the change ceiling's ``files`` and ``lines`` under their product names.

    Why: ADR 0045. The values move unchanged, so nothing is loosened by the move itself
    (``lowered.py`` judges the new keys against the old); ``test_lines`` is the method's own.
    A project that recorded only one of the two takes the template's for the other.
    """
    text = text or ""
    fresh = tomllib.loads(template_file("quality/thresholds.toml"))["ceilings"]["change"]
    table = tomllib.loads(text).get("ceilings", {}).get("change", {})
    return change_size.with_ceilings(
        text,
        (
            table.get("files", fresh["product_files"]),
            table.get("lines", fresh["product_lines"]),
            table.get("test_lines", fresh["test_lines"]),
        ),
    )


def with_change_ceiling(text: str | None) -> str:
    """The thresholds with the template's ``[ceilings.change]`` live: its comment made a table.

    Why: ADR 0032. A change-size ceiling has nothing to measure at first, so its value is the
    method's own, the template's; the file that kept it as a comment gets it as a table.
    """
    text = text or ""
    table = tomllib.loads(template_file("quality/thresholds.toml"))["ceilings"]["change"]
    live = f"{CHANGE_CEILING}\n" + "".join(f"{key} = {value}\n" for key, value in table.items())
    commented = "".join(f"# {line}\n" for line in live.splitlines())
    above = f"{text.rstrip()}\n\n" if text.strip() else ""
    made = text.replace(commented, live, 1) if commented in text else above + live
    try:
        tomllib.loads(made)
    except tomllib.TOMLDecodeError:
        return text
    return made


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
    for line in project_bite.failures(root):
        if line == f"{project_bite.REGISTRY}: {project_bite.MISSING}":
            yield whole_file(str(project_bite.REGISTRY), project_bite.MISSING)
        else:
            yield agent(line)


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
        yield kept_directory("docs/disputes")
        return
    for _, path in numbered_files(directory):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^- Status: (\S+)$", text, re.MULTILINE)
        if match is None or match.group(1) not in DISPUTE_STATUSES:
            yield agent(f"{path.relative_to(root)}: needs '- Status: open|upheld|rejected'")
        for section in DISPUTE_SECTIONS:
            if not markdown_text.has_section(text, section):
                yield agent(f"{path.relative_to(root)}: has no '{section}' section")


def check_features(root: Path) -> Iterable[Finding]:
    # Why: the pipeline's spec role writes docs/features/<slug>/spec.md; the directory is the
    # method's. The feature's input is a tracker ticket, which the feature preflight checks.
    if not (root / "docs/features").is_dir():
        yield kept_directory("docs/features")


def check_pr_template(root: Path) -> Iterable[Finding]:
    relative = pr_template_path(root)
    text = read(root / relative)
    wanted = template_file(DEFAULT_PR_TEMPLATE)
    if text is None:
        yield whole_file(
            relative, "missing (the binding names the pull request template)", DEFAULT_PR_TEMPLATE
        )
        return
    for heading in PR_SECTIONS:
        if not markdown_text.has_section(text, heading):
            problem = f"has no '{heading}' section"
            yield missing_section(relative, problem, heading, template_text=wanted)


# Why: the pin comes first, since an upgrade reads the plugin's commit from it.
CHECKS: tuple[Check, ...] = (
    check_plugin_pin,
    check_agent_instructions,
    check_glossary,
    check_brief,
    check_adrs,
    check_binding,
    check_architecture,
    check_triggers,
    check_dependencies,
    check_suppressions,
    check_signs,
    check_pins,
    check_mutation,
    check_operability,
    check_speed,
    check_manifest,
    check_default_branch,
    check_doors,
    check_merge_workflow,
    check_weekly_workflow,
    check_toolchain,
    check_settings,
    check_commit_hooks,
    check_runner,
    check_installs,
    check_quality,
    check_bites,
    check_incidents,
    check_disputes,
    check_features,
    check_pr_template,
)


def rule_of(check: Check) -> str:
    """The name an owed entry gives the check that raised a finding."""
    return check.__name__.removeprefix("check_")


def raised(root: Path) -> list[tuple[Check, Finding]]:
    """Every failure of every check, in the order of ``CHECKS``, with the check that raised it.

    The owed ledger's own failures come last: an entry whose finding is gone, and a ledger that
    does not read.
    """
    found = [
        (check, replace(failure, rule=rule_of(check)))
        for check in CHECKS
        for failure in check(root)
    ]
    if any(isinstance(failure.fix, Auto) for _, failure in found):
        message = "a check offers an Edit: only the trial makes a finding auto"
        raise TypeError(message)
    return found + ledger_raised(root, [failure.key() for _, failure in found])


def ledger_raised(root: Path, keys: list[owed.Key]) -> list[tuple[Check, Finding]]:
    """The owed ledger's failures, each with a check that raises it again on an edited tree.

    Why: an entry that excuses nothing would excuse the same finding if it came back, so it goes,
    and the upgrade drops it. No check reads the ledger, so dropping an entry changes no other
    finding, and the check that judges the drop compares the edited ledger with ``keys``.
    """

    def check_owed(tree: Path) -> Iterator[Finding]:
        ledger = owed.read(tree)
        for problem in ledger.problems:
            yield Finding(str(owed.LEDGER), problem, Agent(), rule=owed.OWN)
        _, stale = owed.excused(keys, ledger.entries)
        if not stale:
            return
        count = stale.total()
        named = [
            f"'{problem}' for {path} ({rule})" + ("" if extra == 1 else f", {extra} entries")
            for (rule, path, problem), extra in sorted(stale.items())
        ]
        if count == 1:
            problem = f"an entry owes {named[0]}, which is no longer found"
            does = "drop the entry whose finding is fixed"
        else:
            problem = f"{count} entries owe what is no longer found: {'; '.join(named)}"
            does = f"drop the {count} entries whose findings are fixed"
        yield Finding(
            str(owed.LEDGER),
            f"{problem}; an entry goes once its finding is fixed",
            Edit(does, owed.kept(Counter(keys)), Agent()),
            rule=owed.OWN,
        )

    return [(check_owed, failure) for failure in check_owed(root)]


def findings(root: Path, *, owned: bool = False) -> list[Finding]:
    """Every failure of every check, each edit a check offers tried by ``tried``.

    The edits are tried in a copy of what git sees of ``root``, or in ``root`` itself when the
    caller ``owned`` it: the upgrade's own copy, which nothing else reads while it runs.
    """
    found = raised(root)
    if not any(isinstance(failure.fix, Edit) for _, failure in found):
        return [failure for _, failure in found]
    with contextlib.ExitStack() as stack:
        tree = root
        if not owned:
            tree = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="ratchet-trial-")))
            copied(root, tree)
        return [tried(tree, check, failure) for check, failure in found]


def failures(root: Path) -> list[str]:
    return [str(finding) for _, finding in raised(root)]


def owed_split(root: Path, found: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """The findings that are red, and those an entry of the owed ledger excuses (ADR 0037)."""
    excused, _ = owed.excused([finding.key() for finding in found], owed.read(root).entries)
    red = [finding for finding, hit in zip(found, excused, strict=True) if not hit]
    return red, [finding for finding, hit in zip(found, excused, strict=True) if hit]


def not_running(root: Path, found: Sequence[Finding]) -> list[str]:
    """The checks the binding asks for that do not run yet, as the owner reads them, once each.

    Why: ADR 0037. An owed finding stops no merge, so the owner hears what stays off until it is
    fixed: a Gates row that is missing, a gate that no tier or CI job runs, the weekly run, and a
    `later:` row whose kind the Triggers table cannot count, which never starts by itself.
    """
    binding = read(root / BINDING_PATH) or ""
    kinds, _ = triggers.parse(binding)
    stuck = [
        row.question
        for row in project_bite.gate_rows(binding)
        if (waits := application_types.LATER.match(row.tool))
        and set(application_types.code_kinds(waits.group(1).strip())) - set(kinds)
    ]
    return list(dict.fromkeys([*(finding.stops for finding in found if finding.stops), *stuck]))


def run(root: Path) -> int:
    found = findings(root)
    red, owing = owed_split(root, found)
    for finding in red:
        print(finding)
    for finding in owing:
        print(f"owed: {finding}")
    autos = sum(isinstance(finding.fix, Auto) for finding in [*red, *owing])
    fixer = f"; `upgrade.py --apply` makes {autos} of them" if autos else ""
    since = f"; {len(owing)} owed since an upgrade, listed in {owed.LEDGER}" if owing else ""
    off = not_running(root, found)
    waiting = f"; not running until fixed: {', '.join(off)}" if off else ""
    print(f"conformance: {'red' if red else 'green'} ({len(red)} failures{since}{waiting}{fixer})")
    return 1 if red else 0


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
        *install_mutations(),
        *suppression_mutations(),
        *signs_mutations(),
        *pins_mutations(),
        *mutation_gate_mutations(),
        *dependency_mutations(),
        *weekly_mutations(),
        *record_mutations(),
        *trigger_mutations(),
        *upgrade_mutations(),
        *owed_mutations(),
        *architecture_mutations(),
    ]


def architecture_mutations() -> list[Mutation]:
    """The binding's Architecture section and the decision record it names (ADR 0049)."""

    def record_edit(old: str, new: str) -> Callable[[Path], None]:
        def mutate(root: Path) -> None:
            path = root / SELF_TEST_RECORD
            path.write_text(path.read_text().replace(old, new))

        return mutate

    def no_record(root: Path) -> None:
        (root / SELF_TEST_RECORD).unlink()

    return [
        ("architecture section removed", rebind("## Architecture\n", "## Architect\n")),
        ("architecture record missing", no_record),
        ("architecture parts removed", rebind(SELF_TEST_PARTS, "")),
        (
            "architecture part depends on an undeclared one",
            rebind("`src/cli/**` | rules |", "`src/cli/**` | ghost |"),
        ),
        ("architecture record has one alternative", record_edit("- **One file**", "**One file**")),
        ("architecture record says not who chose", record_edit("- Chosen by: the owner", "")),
        (
            "architecture record has no reversal cost",
            record_edit(architecture.REVERSAL_SECTION, "## Reversal"),
        ),
    ]


def trigger_mutations() -> list[Mutation]:
    """The `later:` cells, the Life events line, the Triggers table and the incident records."""
    binding = BINDING_PATH
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
        ("binding without a Tests section", rewrite(BINDING_PATH, TESTS_SECTION, "## Trials")),
        (
            "thresholds without a live change-size ceiling",
            rewrite("quality/thresholds.toml", CHANGE_CEILING, f"# {CHANGE_CEILING}"),
        ),
        (
            "thresholds whose change ceiling still counts tests",
            rewrite("quality/thresholds.toml", "product_files", "files"),
        ),
        (
            "binding without a Test paths section",
            rewrite(BINDING_PATH, change_size.SECTION, "## Tested at"),
        ),
        (
            "binding whose Test paths section is the template's",
            rewrite(BINDING_PATH, "- Tests: `tests/**`", "- Tests: <x>"),
        ),
        (
            "binding without the tier0-tree row",
            rewrite(BINDING_PATH, "| tier0-tree | `just tier0-tree` |\n", ""),
        ),
        (
            "binding without a row the catalogue gained",
            rewrite(
                BINDING_PATH,
                "| backup restores | a tool | weekly | | `just restore-drill` |\n",
                "",
            ),
        ),
        (
            "binding without the triggers entry point",
            rewrite(BINDING_PATH, "| triggers | `just triggers` |\n", ""),
        ),
        ("justfile without a recipe the binding names", rewrite("justfile", "bite-one gate:", "")),
        (
            "justfile whose tier 1 never runs a gate the binding binds",
            rewrite("justfile", " conformance triggers incidents ", " conformance triggers "),
        ),
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
    ci = str(WORKFLOWS / "ci.yml")
    return [
        ("AGENTS.md without the method stanza", rewrite("AGENTS.md", "ratchet:method", "nothing")),
        (
            "binding without the conformance entry point",
            rewrite(BINDING_PATH, "| conformance |", "| conform |"),
        ),
        (
            "pull-request template missing a section",
            rewrite(DEFAULT_PR_TEMPLATE, "## Guard paths", "## Guards"),
        ),
        (
            "CI workflow checking the plugin out at a placeholder",
            rewrite(ci, "0" * 40, PLUGIN_CHECKOUT_PLACEHOLDER, count=1),
        ),
        (
            "glossary still naming <project>",
            rewrite("GLOSSARY.md", "the project", GLOSSARY_PLACEHOLDER, count=1),
        ),
    ]


def both(first: Callable[[Path], None], second: Callable[[Path], None]) -> Callable[[Path], None]:
    """A mutation that makes two."""

    def mutate(root: Path) -> None:
        first(root)
        second(root)

    return mutate


def rebind(old: str, new: str) -> Callable[[Path], None]:
    """A mutation that rewrites one piece of the binding."""
    return rewrite(BINDING_PATH, old, new)


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
            rebind("| format | a tool |", "| format | none: no formatter; ADR 0044 |"),
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
    """A mutation that rewrites the first piece of the bite registry that matches."""
    return rewrite(str(project_bite.REGISTRY), old, new, count=1)


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
        ("CI workflow without concurrency control", rewrite(ci, "concurrency:", "serial:")),
        ("CI workflow without a cache", rewrite(deep, "cache", "store")),
        ("CI workflow without a shard matrix", rewrite(ci, SHARD_MATRIX, "matrix.part")),
    ]


def operability_mutations() -> list[Mutation]:
    def drop_flag_registry(root: Path) -> None:
        (root / FLAG_REGISTRY).unlink()

    return [
        (
            "binding without the telemetry row",
            rebind("| telemetry emitted |", "| tracing |"),
        ),
        ("binding without a Feature flags section", rebind("## Feature flags", "## Toggles")),
        ("repository without the flag registry", drop_flag_registry),
    ]


def hook_mutations() -> list[Mutation]:
    hooks = str(COMMIT_HOOKS)
    named = f"- Commit hooks: `{COMMIT_HOOKS}`"
    renamed = rebind(named, "- Commit hooks: `lefthook.yml`, installed by `lefthook`")

    def another_mechanism_with_one_hook(root: Path) -> None:
        renamed(root)
        (root / "lefthook.yml").write_text("pre-commit:\n  commands:\n    tree: {run: just x}\n")

    return [
        ("binding naming commit hooks that are not there", renamed),
        ("another hook mechanism without the commit-msg hook", another_mechanism_with_one_hook),
        (
            "commit hooks without the commit-msg stage",
            rewrite(hooks, "stages: [commit-msg]", "stages: [pre-push]"),
        ),
        (
            "commit hooks running the whole tier0",
            rewrite(hooks, "entry: just pre-commit", "entry: just tier0"),
        ),
        (
            "hook recipes that cannot answer the probe",
            rewrite("justfile", hooks_installed.PROBE_ENV, "NO_PROBE"),
        ),
    ]


def guard_mutations() -> list[Mutation]:
    manifest, settings = "guards/manifest.toml", str(settings_rules.SETTINGS)
    denied = '"Bash(gh label edit:*)",'
    return [
        (
            "manifest with the harness unlocked",
            rewrite(manifest, 'edit = "deny"', 'edit = "ask"'),
        ),
        (
            "manifest with a group still unbound",
            rewrite(manifest, 'paths = [\n    "quality/**",\n]', "paths = []"),
        ),
        (
            "settings denying what the hook allows",
            rewrite(settings, denied, denied + '\n      "Bash(gh label create:*)",'),
        ),
        (
            "settings missing a fixed rule",
            rewrite(settings, '      "Edit(/.claude/hooks/**)",\n', ""),
        ),
    ]


def rewrite(name: str, old: str, new: str, *, count: int = -1) -> Callable[[Path], None]:
    """A mutation that rewrites ``count`` pieces of a template file, and refuses when it is gone."""

    def mutate(root: Path) -> None:
        path = root / name
        text = path.read_text()
        if old not in text:
            message = f"the self-test's {name} has no {old!r} to mutate"
            raise SystemExit(message)
        path.write_text(text.replace(old, new, count))

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
            rewrite(BINDING_PATH, "| merge | `just merge` |\n", ""),
        ),
    ]


def weekly_mutations() -> list[Mutation]:
    def no_weekly_workflow(root: Path) -> None:
        (root / WORKFLOWS / WEEKLY_WORKFLOW).unlink()

    workflow = str(WORKFLOWS / WEEKLY_WORKFLOW)
    unset_toolchain = rewrite(workflow, TOOLCHAIN_STEP, TOOLCHAIN_PLACEHOLDER)
    return [
        ("forge without the weekly workflow", no_weekly_workflow),
        ("weekly workflow that runs on no schedule", rewrite(workflow, "schedule:", "push:")),
        (
            "weekly workflow that cannot open an issue",
            rewrite(workflow, "issues: write", "issues: read"),
        ),
        ("weekly workflow whose toolchain was never set up", unset_toolchain),
        (
            "binding without the weekly entry point",
            rewrite(BINDING_PATH, "| weekly | `just weekly` |\n", ""),
        ),
    ]


def pin_mutations() -> list[Mutation]:
    binding, pinned = BINDING_PATH, "0" * 40
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


def install_mutations() -> list[Mutation]:
    """A binding that says an install comes first, and a justfile that does not keep to it."""
    lockfile = "self-test.lock"
    kept = "- Install: `none: the self-test tools need no install`"

    def installing(
        *, names: bool = True, depends: bool = True, tiers: bool = True, listed: bool = True
    ) -> Callable[[Path], None]:
        def mutate(root: Path) -> None:
            (root / lockfile).write_text("locked\n", encoding="utf-8")
            binding = root / BINDING_PATH
            lines = [
                "- Install: `just install`",
                f"- Lockfile: `{lockfile}`",
                f"- Needed by: `{'fmt-check' if listed else 'no-such-recipe'}`",
            ]
            binding.write_text(
                binding.read_text(encoding="utf-8").replace(kept, "\n".join(lines)),
                encoding="utf-8",
            )
            path = root / "justfile"
            text = path.read_text(encoding="utf-8")
            named = lockfile if names else "other.lock"
            text = text.replace("install:\n    @true", f"install:\n    @cmp -s {named} .stamp")
            text = text.replace("fmt-check:\n", f"fmt-check:{' install' if depends else ''}\n", 1)
            if tiers:
                text = re.sub(r"^([\w-]+: plugin-pin)$", r"\1 install", text, flags=re.MULTILINE)
            path.write_text(text, encoding="utf-8")

        return mutate

    return [
        (
            "install recipe that does not name the lockfile",
            installing(names=False),
        ),
        (
            "recipe the binding says needs an install that does not depend on it",
            installing(depends=False),
        ),
        (
            "tiers that start their gates before anything installs",
            installing(tiers=False),
        ),
        (
            "Install section listing a recipe the justfile lacks",
            installing(listed=False),
        ),
        ("binding without an Install section", rebind(installs.SECTION, "## Setup")),
        (
            "Install section left as the template wrote it",
            rebind(kept, "- Install: <`just install`>"),
        ),
        ("Install section that says none with no reason", rebind(kept, "- Install: `none: x`")),
    ]


def unguarded_lockfile(root: Path) -> None:
    """A lockfile in a tree whose guard manifest names no such file."""
    (root / "self-test.lock").write_text("locked\n", encoding="utf-8")


def dependency_mutations() -> list[Mutation]:
    """The Dependencies section held to the justfile that reads it (ADR 0044)."""
    none = "`none: the self-test has no such file`"
    entry = f"- Entry point: {none}"
    named = "- Entry point: `just dependencies`"
    return [
        (
            "binding without a Dependencies section",
            rebind(dependencies.SECTION, "## Packages"),
        ),
        ("Dependencies section left as the template wrote it", rebind(entry, "- Entry point: <x>")),
        (
            "Dependencies section that says none with no reason",
            rebind(f"- Manifests: {none}", "- Manifests: `none: x`"),
        ),
        (
            "Dependencies section naming files without backticks",
            rebind(f"- Lockfiles: {none}", "- Lockfiles: a.lock"),
        ),
        (
            "Dependencies section with no registries line",
            rebind("- Registries: `none`", "- Registries: <x>"),
        ),
        (
            "entry point with no recipe behind it",
            both(rebind(entry, named), rewrite("justfile", "dependencies dir:", "elsewhere dir:")),
        ),
        (
            "declared lockfile that no guard group covers",
            both(rebind(f"- Lockfiles: {none}", "- Lockfiles: `*.lock`"), unguarded_lockfile),
        ),
        ("entry point whose recipe is still the template's", rebind(entry, named)),
        (
            "entry point whose recipe takes no directory",
            both(
                rebind(entry, named),
                rewrite("justfile", "dependencies dir:", "dependencies:"),
            ),
        ),
    ]


def suppression_mutations() -> list[Mutation]:
    """The Suppressions section held to the Gates rows it speaks for (ADR 0042)."""
    row = f"| strict types | `{SELF_TEST_KIND}` |"
    kind = f"| {SELF_TEST_KIND} | `self-test: silence` | rises |"
    return [
        ("binding without a Suppressions section", rebind(suppressions.SECTION, "## Silenced")),
        (
            "bound gate with no line in the Suppressions section",
            rebind(row + "\n", ""),
        ),
        (
            "bound gate naming a suppression kind with no row",
            rebind(row, "| strict types | `never-listed` |"),
        ),
        ("bound gate that says none with no reason", rebind(row, "| strict types | none: x |")),
        (
            "suppression kind whose pattern git cannot read",
            rebind(kind, f"| {SELF_TEST_KIND} | `(` | rises |"),
        ),
        (
            "suppression kind with a rule that is neither rises nor never",
            rebind(kind, f"| {SELF_TEST_KIND} | `self-test: silence` | sometimes |"),
        ),
        (
            "Suppressions rows left as the template wrote them",
            rebind(kind, "| <suppression kind> | <`pattern`> | rises |"),
        ),
    ]


def signs_mutations() -> list[Mutation]:
    """The Life event signs table held to the events whose signs the stack decides (ADR 0050)."""
    row = "| deployed | none: the self-test has no such file |"
    section = application_types.SIGNS_SECTION
    return [
        ("binding without a Life event signs section", rebind(section, "## Marks")),
        ("Life event signs table with no row for an event", rebind(row + "\n", "")),
        ("Life event signs row for an event the catalogue lacks", rebind(row, "| famous | `a` |")),
        (
            "Life event signs row that says none with no reason",
            rebind(row, "| deployed | none: x |"),
        ),
        ("Life event signs row with no globs", rebind(row, "| deployed | a file |")),
        (
            "Life event signs row left as the template wrote it",
            rebind(row, "| deployed | <globs of the tracked files that show where it runs> |"),
        ),
    ]


def pins_mutations() -> list[Mutation]:
    """The Image pins section held to a table the pins gate can read (ADR 0050)."""
    row = SELF_TEST_PINS
    return [
        ("binding without an Image pins section", rebind(pins.SECTION, "## Digests")),
        ("Image pins row that says none with no reason", rebind(row, "| none: x | none |")),
        ("Image pins row whose pattern is no regex", rebind(row, "| `a/**` | `(` |")),
        ("Image pins row whose pattern has no group", rebind(row, "| `a/**` | `FROM x` |")),
        (
            "Image pins row left as the template wrote it",
            rebind(
                row,
                "| <`globs` of the files that name an image, or `none: <reason>`> | <`regex`> |",
            ),
        ),
    ]


def mutation_gate_mutations() -> list[Mutation]:
    """A recipe that hands the mutation gate a tool's own report (ADR 0050)."""

    def passing_a_report(root: Path) -> None:
        text = (root / "justfile").read_text(encoding="utf-8")
        script = '"{{ratchet_plugin}}/scripts/mutation_survivors.py"'
        recipe = f"\nmutation-own:\n    python3 {script} --tool x --report r.json\n"
        (root / "justfile").write_text(text + recipe, encoding="utf-8")

    return [("mutation recipe that passes a tool's own report", passing_a_report)]


def owed_mutations() -> list[Mutation]:
    """An owed entry no finding matches: it would excuse that finding if it came back."""

    def stale(root: Path) -> None:
        entry = owed.Entry("justfile", "runner", "a finding no check raises", "0.0.0")
        (root / owed.LEDGER).write_text(owed.render([entry]), encoding="utf-8")

    return [("owed ledger with an entry no finding matches", stale)]


def record_mutations() -> list[Mutation]:
    def adr_without_refuter(root: Path) -> None:
        (root / "docs/adr/0003-plain.md").write_text("# We keep it plain\n\nBecause.\n")

    def adr_with_an_empty_refuter(root: Path) -> None:
        (root / "docs/adr/0003-empty.md").write_text(
            f"# We keep it empty\n\nBecause.\n\n{REFUTATION_SECTION}\nNone.\n"
        )

    def features_directory_removed(root: Path) -> None:
        (root / "docs/features/.gitkeep").unlink()
        (root / "docs/features").rmdir()

    def gap_adrs(root: Path) -> None:
        (root / "docs/adr/0004-gap.md").write_text(f"# A gap\n\n{REFUTATION_SECTION}\nNone.\n")

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
TEST_PATHS_PLACEHOLDER = re.compile(r"^- Tests: <[^\n]*>$", re.MULTILINE)
TRIGGERS_PLACEHOLDER = re.compile(r"^\| <kind> \|[^\n]*$", re.MULTILINE)
SPEED_PLACEHOLDER = re.compile(r"^(- [A-Z][a-z ]+: )<[^\n]*>$", re.MULTILINE)
INSTALL_PLACEHOLDER = re.compile(r"^- Install: <[^\n]*>$", re.MULTILINE)
DEPENDENCY_PLACEHOLDER = re.compile(
    r"^- (Manifests|Lockfiles|Entry point): <[^\n]*>$", re.MULTILINE
)
REGISTRIES_PLACEHOLDER = re.compile(r"^- Registries: <[^\n]*>$", re.MULTILINE)
SUPPRESSION_KIND_PLACEHOLDER = re.compile(r"^\| <suppression kind> \|[^\n]*$", re.MULTILINE)
SUPPRESSION_QUESTION_PLACEHOLDER = re.compile(r"^\| <question> \|[^\n]*$", re.MULTILINE)
SELF_TEST_KIND = "self-test-silence"
SIGNS_PLACEHOLDER = re.compile(r"^\| (deployed|irreplaceable-data) \| <[^\n]*$", re.MULTILINE)
PINS_PLACEHOLDER = re.compile(r"^\| <`globs` of the files that name an image[^\n]*$", re.MULTILINE)
SELF_TEST_PINS = "| none: the self-test names no image | none |"
DECISION_PLACEHOLDER = re.compile(r"^- Decision: <[^\n]*>$", re.MULTILINE)
PARTS_PLACEHOLDER = re.compile(r"^\| <part> \|[^\n]*$", re.MULTILINE)
SELF_TEST_RECORD = "docs/adr/0002-the-tool-is-one-command-over-its-rules.md"
SELF_TEST_PARTS = "| rules | `src/rules/**` | nothing |\n| command | `src/cli/**` | rules |"
SELF_TEST_ARCHITECTURE = f"""\
# The tool is one command over a module of rules

{architecture.DECISION_SECTION}

A command line tool: a module of rules that does no I/O, and the command around it.

- Chosen by: the owner

{architecture.OPTIONS_SECTION}

- **A service**: set aside before asking. Nobody but the owner calls it.
- **One file**: offered. The rules could not be tested without the command.

{architecture.REVERSAL_SECTION}

The rules move as they are; the command is rewritten, about a day, and nothing else is redone.

{REFUTATION_SECTION}

Wrong if a change to the rules keeps needing a change to the command.
"""


def fill_binding(text: str) -> str:
    """A command line tool's binding: one row its type leaves out, a tool in every other."""
    text = text.replace(PLUGIN_PIN_PLACEHOLDER, f"0.0.0 at {'0' * 40}")
    text = TYPES_PLACEHOLDER.sub("- Application types: cli", text)
    text = TEST_PATHS_PLACEHOLDER.sub("- Tests: `tests/**`", text)
    text = DECISION_PLACEHOLDER.sub(f"- Decision: `{SELF_TEST_RECORD}`", text)
    text = PARTS_PLACEHOLDER.sub(SELF_TEST_PARTS, text)
    text = text.replace("| contract drift | |", "| contract drift | none: not a service |")
    # Why: ADR 0028. One row waits on its trigger and so needs no bite; the rest are bound.
    text = text.replace(
        "| rolls back safely | |",
        "| rolls back safely | later: life:deployed and code:stored-format |",
    )
    text = TRIGGERS_PLACEHOLDER.sub(triggers_rows, text)
    text = DEPENDENCY_PLACEHOLDER.sub(r"- \1: `none: the self-test has no such file`", text)
    text = REGISTRIES_PLACEHOLDER.sub("- Registries: `none`", text)
    text = INSTALL_PLACEHOLDER.sub("- Install: `none: the self-test tools need no install`", text)
    text = SUPPRESSION_KIND_PLACEHOLDER.sub(
        f"| {SELF_TEST_KIND} | `self-test: silence` | rises |", text
    )
    text = SUPPRESSION_QUESTION_PLACEHOLDER.sub(suppression_rows, text)
    text = SIGNS_PLACEHOLDER.sub(r"| \1 | none: the self-test has no such file |", text)
    text = PINS_PLACEHOLDER.sub(SELF_TEST_PINS, text)
    text = EMPTY_TOOL_CELL.sub(r"\1 a tool |", text)
    return SPEED_PLACEHOLDER.sub(r"\1stated for this stack", text)


def triggers_rows(_: re.Match[str]) -> str:
    """A row for each kind of code a question asked of a command line tool is triggered by."""
    kinds = sorted(application_types.load().kinds_required({"cli"}))
    return "\n".join(f"| {kind} | none: the self-test has none | |" for kind in kinds)


def suppression_rows(_: re.Match[str]) -> str:
    """A row for each question a directive can silence, naming the self-test's one kind."""
    return "\n".join(
        f"| {question} | `{SELF_TEST_KIND}` |"
        for question in sorted(application_types.load().directed)
    )


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
    settings_rules.regenerate(root)
    binding = root / BINDING_PATH
    binding.write_text(fill_binding(binding.read_text()))
    registry = root / project_bite.REGISTRY
    registry.write_text(registry.read_text() + fill_registry(binding.read_text()))
    (root / SELF_TEST_RECORD).write_text(SELF_TEST_ARCHITECTURE)
    pinned = {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40, TOOLCHAIN_PLACEHOLDER: TOOLCHAIN_STEP}
    fills = {
        "GLOSSARY.md": {GLOSSARY_PLACEHOLDER: "the project"},
        str(WORKFLOWS / "ci.yml"): pinned,
        str(WORKFLOWS / "deep.yml"): pinned,
        str(WORKFLOWS / MERGE_WORKFLOW): pinned,
        str(WORKFLOWS / WEEKLY_WORKFLOW): pinned,
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
