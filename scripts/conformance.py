#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import difflib
import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import application_types
import architecture
import budget
import change_size
import changed_tests
import ci_jobs
import comments
import documents
import formal_model
import gatelib
import hooks_installed
import installs
import justfile_text
import markdown_text
import owed
import pins
import plugin_pin
import project_bite
import report
import runrecord
import settings_rules
import suites
import suppressions
import triggers
import unproven
from ratchet_guards import committed

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
    "ci-job",
    "check",
    "check-fast",
    "check-deep",
    "ratchet",
    "ratchet-lowered",
    "snapshots",
    "snapshots-check",
    "suppressions",
    "comments",
    comments.LISTER,
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
SPEED_PARTS = (
    "Parallel",
    "One pipeline",
    "Shards",
    "Cache",
    "Incremental",
    "Affected only",
    "Ceilings",
)
NONE_WITH_REASON = re.compile(r"none: \S.*")
SPEED_PART = re.compile(r"^- ([A-Z][a-z ]+):[ \t]*(.*)$", re.MULTILINE)
SHARD_COUNT = re.compile(r"[1-9]\d*")
TIER_WORKFLOWS = ("ci.yml", "deep.yml")
TIER_LISTS = {
    "ci.yml": (
        ("tier1_gates", "every gate your `tier1` recipe runs"),
        ("tier1_sharded", 'the gates of tier 1 whose tool can run one `SHARD` of `SHARDS`, or ""'),
        ("tier1_jobs", "the jobs CI runs tier 1 as, with `rest` for every gate no other job names"),
    ),
    "deep.yml": (
        ("tier2_gates", "every gate your `tier2` recipe runs"),
        ("tier2_sharded", 'the gates of tier 2 whose tool can run one `SHARD` of `SHARDS`, or ""'),
    ),
}
MERGE_WORKFLOW = "merge.yml"
TIER1_FLAG = "--tier1"
TIER1_ARGUMENT = '--tier1 "$tier1"'
TIER2_FLAG = '--tier2 "$tier2"'
DEEP_ARGUMENT = '--deep "$answer"'
DEEP_FINISH_MARKER = "cancel-in-progress: false"
DEEP_SPEAKS_MARKER = 'forge_runs.py" speaks'
DEEP_SHARD_CHECK_MARKER = "--shard-check"
DEEP_SLICE_REQUIREMENTS = (
    ("needs.shape.outputs.tier2_long", "runs the gates of `tier2_sharded`"),
    ("needs.shape.outputs.shards", "runs every slice `1..N`"),
    ("SHARD: ${{ matrix.shard }}", "passes `SHARD` to each slice"),
    ("SHARDS: ${{ needs.shape.outputs.count }}", "passes `SHARDS` to each slice"),
)
SHARD_FLAG = re.compile(
    r"""--shard(?:=|[ \t]+)(?:"""
    r"""["']?\$\{SHARD:-1\}/\$\{SHARDS:-1\}["']?"""
    r"""|\{\{[ \t]*shard[ \t]*\}\}/\{\{[ \t]*shards[ \t]*\}\})"""
)
SHARDED_BITE_RECIPES = (
    ("tier1_sharded", "bite"),
    ("tier2_sharded", "bite-all"),
)
OLD_BITE_RECIPES = {
    "bite": (
        'bite: _plugin\n    {{plugin_py}} "{{ratchet_plugin}}/scripts/project_bite.py" --changed'
    ),
    "bite-all": (
        "bite-all: _plugin\n"
        '    {{plugin_py}} "{{ratchet_plugin}}/scripts/bite.py"\n'
        '    {{plugin_py}} "{{ratchet_plugin}}/scripts/project_bite.py"'
    ),
}
DEEP_TRIGGER = "workflows: [ci, deep]"
DEEP_DISPATCH = "workflow_dispatch:"
DEEP_FLAG = "--deep"
DEEP_MERGE_MARKERS = frozenset({DEEP_TRIGGER, DEEP_DISPATCH, DEEP_FLAG})
KEPT_BRANCH = "base=$branch"
RUN_VARIABLE = "RATCHET_RUN"
RUN_UNEXPORTED = re.compile(rf"{RUN_VARIABLE}[ \t]*:=")
MERGE_SHAPE = (
    ("workflow_run:", "runs it after `ci`, as the default branch holds it"),
    ("merge_ready.py", "asks the merge judge"),
    ("forge_runs.py", "says which `ci` and `deep` runs count (ADR 0024)"),
    (
        "--tier1",
        "leaves every merge to the owner while `ci` is red on the default branch (ADR 0069)",
    ),
    (KEPT_BRANCH, "keeps a merged branch while an open pull request targets it (ADR 0083)"),
    (DEEP_TRIGGER, "runs the merge job when a deep run finishes as well as `ci` (ADR 0077)"),
    (DEEP_DISPATCH, "lets the deep workflow wake the merge job when a deep run ends (ADR 0077)"),
    (DEEP_FLAG, "reads every deep run and holds on a declared bound or red (ADR 0077)"),
    ("issues: write", "may close the issues a merged change says it closes"),
    ("closingIssuesReferences", "closes the issues a merged change says it closes"),
    ("contents: write", "may merge what the judge passes"),
    ("pull-requests: write", "may say what the judge found on a pull request"),
    ("actions: write", "may start tier 2 on the default branch after a merge"),
)
WEEKLY_WORKFLOW = "weekly.yml"
WEEKLY_SHAPE = (
    ("schedule:", "runs the weekly gates on a schedule"),
    ("issues: write", "may open the issue a finding gets"),
    ("just weekly", "runs the weekly gates"),
    ("gh issue create", "opens the issue a finding gets"),
)
JOB_LIST_SHAPE = ("ci_jobs.py", "turns the justfile's job list into the jobs tier 1 runs")
CI_RUN_MARKER = "RATCHET_RUN: ${{ github.run_id }}"
CI_LOOP_MARKER = 'forge_runs.py" loop'
CORE_CI_JOBS = frozenset({"proven", "tier0", "shape", "tier1", "tier1-long"})
TIER_STEP_SHAPE = {
    "ci.yml": (JOB_LIST_SHAPE,),
    "deep.yml": (
        ("just --evaluate", "turns the justfile's tier lists into the jobs a tier runs"),
        (DEEP_SHARD_CHECK_MARKER, "proves every fault of quality/bites.toml lands in one slice"),
        (DEEP_FINISH_MARKER, "lets a deep run that started finish instead of cancelling it"),
        (
            DEEP_SPEAKS_MARKER,
            "says which default-branch merges each deep run speaks for and wakes the merge job",
        ),
    ),
}
OLD_SHAPE_STEP = (
    "      - uses: extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3 # v4",
    "      - id: shards",
    "        run: |",
    (
        "          count=\"$(sed -n 's/^- Shards: *\\([1-9][0-9]*\\) *$/\\1/p'"
        ' docs/process/binding.md)"'
    ),
    (
        '          if [ -z "$count" ]; then echo "docs/process/binding.md:'
        " no '- Shards: N' line\"; exit 1; fi"
    ),
    '          echo "count=$count" >> "$GITHUB_OUTPUT"',
    '          echo "list=[$(seq -s, 1 "$count")]" >> "$GITHUB_OUTPUT"',
    (
        '          json() { out=""; for item in $1; do out="$out,\\"$item\\"";'
        ' done; echo "[${out#,}]"; }'
    ),
    '          long="$(just --evaluate tier1_sharded)"; short=""',
    "          for gate in $(just --evaluate tier1_gates); do",
    '            case " $long " in *" $gate "*) ;; *) short="$short $gate" ;; esac',
    "          done",
    '          echo "tier1=$(json "$short")" >> "$GITHUB_OUTPUT"',
    '          echo "tier1_long=$(json "$long")" >> "$GITHUB_OUTPUT"',
)
OLD_NEEDS = "    needs: [tier0, shape]"
NEW_NEEDS = "    needs: shape"
OLD_RUN, NEW_RUN = ' -- just "${{ matrix.gate }}"', ' -- just ci-job "${{ matrix.gate }}"'
EXEMPT_STEPS = (
    (
        "contents: read",
        "every workflow reads its own checkout, and a project without it fails at the checkout",
    ),
    (
        "actions: read",
        "the proven check reads completed pull request runs and their artifacts on a push",
    ),
    (
        "already_proven.py",
        "skips default-branch push CI when a pull request run already settled tree and base",
    ),
    (
        "actions: write",
        "the deep workflow dispatches the merge workflow when it finishes",
    ),
    (
        "cache_restore.py",
        "prints and records how many bytes the CI cache restored",
    ),
    (
        CI_LOOP_MARKER,
        "judges the run's work path against the loop budget",
    ),
)
DOOR_MEANINGS = {
    "change": "a path whose every change is final, such as a migration or infrastructure code",
    "rewrite": "a file the default branch holds that is never rewritten, such as a golden fixture",
    "removal": "a path whose loss breaks a consumer, such as a contract or a public snapshot",
}
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
OPEN_RECORD = (
    "The record of the slow checks run after each merge is not yet closed to agents. Until it"
    " is, an agent could write a passing result into it, as it could already move the default"
    " branch itself. To close it, an agent adds it to guards/manifest.toml on a branch, and you"
    " decide at that merge."
)
WEEKLY_CHECKS = "the weekly checks"
SHARD_MATRIX = "matrix.shard"
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
MECHANICAL_TRIGGERS = {"flag": "| flag | `flags.toml` | `^\\[\\[flag\\]\\]` |"}
OLD_OVER_ENV = "  OVER: ${{ github.event_name == 'pull_request' && 'report' || 'block' }}"
OLD_OVER_FLAG = '--over "$OVER"'
TIMING_LOG = ".ratchet/timing.jsonl"
CEILING_SHAPE = (
    ("ci.yml", "--over report", "reports tier 0 and tier 1 seconds ceilings on every event"),
    ("ci.yml", "--tier", HELD_TO_TIER),
    ("deep.yml", "--tier", HELD_TO_TIER),
)
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
CACHE_USES = "uses: actions/cache"
CACHE_KEY = re.compile(r"^(?P<head>\s*key:\s*)(?P<value>.*?)(?P<note>\s+#.*)?$")
CACHE_ACTION = re.compile(r"^actions/cache(?:/(?:restore|save))?@\S+$")
FLOW_MATRIX = re.compile(r"\bmatrix\s*:")
GHA_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
MATRIX_EXPR = re.compile(r"\bmatrix\s*(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[\s*['\"][^'\"]+['\"]\s*\])")
YAML_JOB_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:\s|$)")
MULTILINE_KEY = frozenset({"|", ">", "|-", ">-", "|+", ">+"})
PER_RUN_PARTS = ("github.sha", "github.run_id")
CI_SHAPE = (("concurrency:", "cancels superseded runs"), ("cache", "caches between runs"))
ADR_STATUS = re.compile(r"^(proposed|accepted|deprecated|rejected|superseded( by ADR-\d{4})?)$")
ADR_FRONT_MATTER = re.compile(
    r"\A(?:---\nstatus: (?P<status>[^\n]+)\n(?:superseded-by: (?P<by>\d{4})\n)?---\n+)?# \S"
)
ADR_STATUS_WORDS = "proposed|accepted|deprecated|rejected|superseded"
ADR_PATH_REF = re.compile(r"docs/adr/(\d{4}-[a-z0-9-]+\.md)\b")
ADR_NUMBER_REF = re.compile(r"\bADR(?:\s+|-)(\d{4})\b")
GLOSSARY_PLACEHOLDER = "<project>"
PLUGIN_PIN_PLACEHOLDER = "<X.Y.Z> at <ratchet-sha> tree <ratchet-tree-id>"
PLUGIN_CHECKOUT_PLACEHOLDER = "<ratchet-sha>"
PIN_RECIPE = "plugin-pin"
PIN_SCRIPT = "plugin_pin.py"
PLUGIN_VARIABLE = "ratchet_plugin"
MARKETPLACE_FALLBACK = ".claude/plugins/marketplaces/ratchet"
PLUGIN_LINE = re.compile(
    r"^(?:(?:plugin_(?:choice|pyc)[ \t]*:=[ \t]*```(?s:.*?)```"
    r"|(?:export[ \t]+)?(?:RATCHET_PLUGIN_(?:DIR|REFUSAL|CHECKED)|PYTHONPYCACHEPREFIX"
    r"|PYTHONDONTWRITEBYTECODE|RATCHET_PYC_(?:NOTE|SAID)|plugin_tree|plugin_pyc_ok|pyc_said"
    r"|plugin_py)[ \t]*:=.*)\n)*"
    rf"{PLUGIN_VARIABLE}[ \t]*:=.*$",
    re.MULTILINE,
)
PLUGIN_GUARD = "_plugin"
INTERPRETER_VARIABLE = "plugin_py"
BARE_SCRIPT_CALL = re.compile(r'(?<![\w-])python3(?= +"\{\{ratchet_plugin\}\}/)')
BARE_STRING_CALL = re.compile(r"""'python3 "' \+ ratchet_plugin""")
BARE_GUARD_DIFF = re.compile(
    r'PYTHONPATH="\{\{ratchet_plugin\}\}/hooks" python3 -m ratchet_guards\.diff(?=[ \t]|$)'
)
GUARD_DIFF_CALL = re.compile(
    r"\{\{plugin_py\}\} -P -c 'import sys; sys\.path[^\n]*?\{\{ratchet_plugin\}\}/hooks\""
)
NO_EXIT_MESSAGE = "[no-exit-message]"
TOOLCHAIN_MARKER = "bind: toolchain setup"
SUITE_SETUP_MARKER = "bind: the setup each later-tier suite needs"
TOOLCHAIN_PLACEHOLDER = (
    f'      - {{ name: "{TOOLCHAIN_MARKER}, pinned, with the setup action\'s own cache on",'
    " run: exit 1 }"
)
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
GUARDED_PATTERNS = {"guards/**"}
DEEP_RECORD = f".git/{unproven.RECORD}"
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
MOVED = "## Moved by the Ratchet upgrade"
MARKETPLACE = Path(__file__).resolve().parents[1] / ".claude-plugin/marketplace.json"
BOLD_NAME = re.compile(r"^- \*\*([^*]+)\*\*")
BACKTICKED_WORD = re.compile(r"`([^`\s]+)`")
ITEM_BREAK = re.compile(r"^(#|[-*+] |\d+[.)] |>|```|~~~|\||<|---|\*\*\*|___)")
RUNS_JUST = re.compile(r"^just\s+@?([A-Za-z_][\w-]*)")
OVER_THE_CHANGE = frozenset({"diff-coverage", "test-counts", "mutation-changed"})
CARRIED_GATES = frozenset({"tests-changed", "comments"})
CHANGED_TESTS_STOPS = "changed tests run"
SHOWN = 3
VARIADIC = re.compile(r"\s[*+]\w")
TIMED = frozenset({"perf", "load"})
LOOP_RECIPES = ("check", "check-fast")
LOOP_BUDGET_FLAG = "--budget loop"


@dataclass(frozen=True)
class Auto:
    does: str
    want: gatelib.Want
    then: tuple[str, ...] = ()


@dataclass(frozen=True)
class Agent:
    start: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


@dataclass(frozen=True)
class Owner:
    ask: str
    assumed: str
    evidence: tuple[str, ...] = ()
    then: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edit:
    does: str
    want: gatelib.Want
    otherwise: Agent | Owner


Fix = Auto | Edit | Agent | Owner


@dataclass(frozen=True)
class Finding:
    path: str
    problem: str
    fix: Fix
    rule: str = ""
    identity: str = ""
    stops: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.fix, Auto | Edit) and under_guards(self.path):
            message = f"{self.path}: an upgrade never writes under guards/ by itself"
            raise ValueError(message)

    def __str__(self) -> str:
        return f"{self.path}: {self.problem}"

    def key(self) -> owed.Key:
        return (self.rule, self.path, self.identity or self.problem)


Check = Callable[[Path], Iterable[Finding]]


def under_guards(path: str) -> bool:
    return PurePosixPath(path).parts[:1] == ("guards",)


def agent(line: str, start: tuple[str, ...] = (), then: tuple[str, ...] = ()) -> Finding:
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
    path, _, problem = line.partition(": ")
    return Finding(path, problem, Owner(ask, assumed, evidence, then))


@contextlib.contextmanager
def made(tree: Path, name: str, want: gatelib.Want) -> Iterator[bool]:
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
    listed = gatelib.listings.set(None) if data is None else None
    try:
        yield True
    finally:
        if listed is not None:
            gatelib.listings.reset(listed)
        if data is not None:
            target.write_bytes(data)
        elif created:
            shutil.rmtree(created[-1])
        else:
            target.unlink()


Raised = dict[tuple[Check, str, bytes], set[tuple[str, str]]]


def tried(
    tree: Path,
    check: Check,
    finding: Finding,
    *,
    raised_after: Raised | None = None,
) -> Finding:
    fix = finding.fix
    if not isinstance(fix, Edit):
        return finding
    raised_after = {} if raised_after is None else raised_after
    cleared = False
    with made(tree, finding.path, fix.want) as changed:
        if changed:
            key = (check, finding.path, (tree / finding.path).read_bytes())
            if key not in raised_after:
                raised_after[key] = {(again.path, again.problem) for again in check(tree)}
            cleared = (finding.path, finding.problem) not in raised_after[key]
    then = fix.otherwise.then if isinstance(fix.otherwise, Agent) else ()
    return replace(finding, fix=Auto(fix.does, fix.want, then) if cleared else fix.otherwise)


def read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def justfile_paths(root: Path) -> list[Path]:
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
    texts = [read(path) or "" for path in justfile_paths(root)]
    return "\n".join(texts) if texts else None


def copied(root: Path, target: Path) -> dict[str, str]:
    foreign = gatelib.copy_seen(root, target)
    home = root.resolve()
    for path in justfile_paths(root):
        lands = path.resolve()
        name = os.path.relpath(path, root)
        if lands.is_relative_to(home) and not os.path.lexists(target / name):
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lands, target / name)
    return foreign


@functools.cache
def template_file(path: str) -> str:
    return read(ADOPT_TEMPLATES / path) or read(INIT_TEMPLATES / path) or ""


def headings(text: str) -> tuple[str, ...]:
    return tuple(line.rstrip() for line in text.splitlines() if line.startswith("## "))


def whole_file(path: str, problem: str, template: str | None = None) -> Finding:
    want = gatelib.created(template_file(template or path))
    return Finding(path, problem, Edit("add the template's file", want, Agent()))


def kept_directory(path: str) -> Finding:
    return whole_file(f"{path}/.gitkeep", f"the directory {path} is missing")


def missing_section(path: str, problem: str, heading: str, *, template_text: str) -> Finding:
    block = markdown_text.padded_tables(
        heading + markdown_text.section(template_text, heading).rstrip()
    )
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


def companions() -> tuple[str, ...]:
    declared = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    (ratchet,) = (entry for entry in declared["plugins"] if entry["name"] == "ratchet")
    return tuple(dependency.partition("@")[0] for dependency in ratchet["dependencies"])


def companion_of(line: str, names: Sequence[str]) -> str | None:
    if not line.startswith("- "):
        return None
    bold = BOLD_NAME.match(line)
    if bold and bold.group(1) in names:
        return bold.group(1)
    ticked = BACKTICKED_WORD.search(line)
    return ticked.group(1) if ticked and ticked.group(1) in names else None


class CompanionItem(NamedTuple):
    start: int
    stop: int
    name: str
    lines: tuple[str, ...]


def item_end(lines: Sequence[str], start: int) -> int:
    stop = start + 1
    index = stop
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if not line[0].isspace() and (index > stop or ITEM_BREAK.match(line)):
            break
        index += 1
        stop = index
    return stop


def companion_items(text: str, names: Sequence[str]) -> Iterator[CompanionItem]:
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines) and not lines[index].startswith("##"):
        name = companion_of(lines[index], names)
        if name is None:
            index += 1
            continue
        stop = item_end(lines, index)
        yield CompanionItem(index, stop, name, tuple(lines[index:stop]))
        index = stop


def plain_companion(name: str) -> str:
    return f"- `{name}`"


def is_plain(item: CompanionItem) -> bool:
    return len(item.lines) == 1 and item.lines[0].rstrip("\r\n") == plain_companion(item.name)


def newline_of(text: str) -> str:
    first = text.splitlines(keepends=True)[:1]
    return first[0][len(first[0].rstrip("\r\n")) :] or "\n" if first else "\n"


def with_moved_items(text: str, items: Sequence[str]) -> str:
    newline = newline_of(text)
    added = [f"{item}{newline}" for item in items]
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    starts = [i for i, line in enumerate(lines) if line.rstrip() == MOVED]
    if not starts:
        return "".join([*lines, newline, MOVED, newline, newline, *added])
    start = starts[-1]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    last = next((i for i in range(end - 1, start, -1) if lines[i].strip()), start)
    lines[last + 1 : last + 1] = added
    return "".join(lines)


def moved_items(text: str) -> list[str]:
    return [line.rstrip("\r") for line in markdown_text.section(text, MOVED).splitlines() if line]


def with_plain_companions(names: Sequence[str]) -> gatelib.Want:
    def want(text: str | None) -> str:
        lines = (text or "").splitlines(keepends=True)
        moved: list[str] = []
        for item in reversed(list(companion_items(text or "", names))):
            if is_plain(item):
                continue
            first = item.lines[0]
            moved[:0] = [line.rstrip("\r\n") for line in item.lines]
            lines[item.start : item.stop] = [
                plain_companion(item.name) + first[len(first.rstrip("\r\n")) :]
            ]
        made = "".join(lines)
        return with_moved_items(made, moved) if moved else made

    return want


def check_companion_lines(root: Path) -> Iterable[Finding]:
    name = "AGENTS.md"
    text = read(root / name)
    if text is None:
        name, text = "CLAUDE.md", read(root / "CLAUDE.md")
    names = companions()
    stale = [item.name for item in companion_items(text or "", names) if not is_plain(item)]
    if stale:
        plugins = ", ".join(stale)
        problem = (
            f"the lines for {plugins} are not exactly `- `<plugin>``; the project's own text on"
            f" them moves to '{MOVED}' and is not deleted"
        )
        edit = Edit(
            f"write each companion line as the plugin's name alone, its text moved to '{MOVED}'",
            with_plain_companions(names),
            Agent(start=tuple(plain_companion(plugin) for plugin in stale)),
        )
        yield Finding(name, problem, edit)


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


def base_adr_paths(root: Path, base: str | None) -> set[str]:
    if base is None:
        return set()
    listed = gatelib.git(root, "ls-tree", "-r", "--name-only", "-z", base, "--", "docs/adr")
    return {path for path in (listed or "").split("\0") if path}


def duplicate_adr_hint(
    num: int, target: int, *, root: Path, files: list[tuple[int, Path]], base_files: set[str]
) -> str:
    dups = [path.relative_to(root).as_posix() for number, path in files if number == num]
    existing = [rel for rel in dups if rel in base_files]
    new_files = [rel for rel in dups if rel not in base_files]
    if len(existing) == 1 and len(new_files) == 1:
        return (
            f"{num:04d} was taken on the default branch ({existing[0]}); "
            f"move {new_files[0]} to {target:04d} with "
            f'python3 "$RATCHET_PLUGIN/scripts/adr_renumber.py" {num:04d} {target:04d}'
        )
    pick = new_files[0] if new_files else dups[-1]
    return (
        f"{num:04d} is shared by {', '.join(dups)}; move one to {target:04d} with "
        f'python3 "$RATCHET_PLUGIN/scripts/adr_renumber.py" --file {pick} {num:04d} {target:04d}'
    )


def adr_numbering_finding(root: Path, files: list[tuple[int, Path]], numbers: list[int]) -> Finding:
    base_message = f"docs/adr: numbering has gaps or duplicates: {numbers}"
    duplicates = sorted(num for num, count in Counter(numbers).items() if count > 1)
    if not duplicates:
        return agent(base_message)
    base_files = base_adr_paths(root, gatelib.base_commit(root))
    next_free = max(numbers) + 1
    hints = [
        duplicate_adr_hint(num, next_free + offset, root=root, files=files, base_files=base_files)
        for offset, num in enumerate(duplicates)
    ]
    return agent(f"{base_message}; {'; '.join(hints)}")


def check_adr_citations(
    root: Path, adrs: AbstractSet[int], adr_names: AbstractSet[str]
) -> Iterable[Finding]:
    targets = [BINDING_PATH, str(gatelib.THRESHOLDS)]
    targets.extend(
        path.relative_to(root).as_posix() for path in sorted((root / "docs/adr").glob("*.md"))
    )
    for rel in targets:
        text = read(root / rel)
        if text is None:
            continue
        for name in dict.fromkeys(ADR_PATH_REF.findall(text)):
            if name not in adr_names:
                yield agent(f"{rel}: references missing ADR docs/adr/{name}")
        if rel == str(gatelib.THRESHOLDS):
            for num_str in dict.fromkeys(ADR_NUMBER_REF.findall(text)):
                if int(num_str) not in adrs:
                    yield agent(f"{rel}: cites ADR {num_str}, which docs/adr does not hold")


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
    adrs = set(numbers)
    if numbers != list(range(1, len(numbers) + 1)):
        yield adr_numbering_finding(root, files, numbers)
    for number, path in files:
        yield from check_adr(number, path, path.relative_to(root), adrs=adrs)
    yield from check_adr_citations(root, adrs, {path.name for _, path in files})


def check_adr(number: int, path: Path, rel: Path, *, adrs: AbstractSet[int]) -> Iterable[Finding]:
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
    if superseded_by is not None and int(superseded_by) not in adrs:
        yield agent(f"{rel}: superseded-by: {superseded_by} names no ADR in docs/adr")
    if not markdown_text.has_section(text, REFUTATION_SECTION):
        yield agent(f"{rel}: has no '{REFUTATION_SECTION}' section")
    elif markdown_text.empty_body(text, REFUTATION_SECTION):
        yield agent(f"{rel}: '{REFUTATION_SECTION}' names no observation that would show it wrong")
    if number == 1 and "Ratchet method" not in text:
        yield agent(f"{rel}: ADR 0001 records adopting the Ratchet method")


def template_binding() -> str:
    return template_file(BINDING_PATH)


def template_rows() -> dict[str, tuple[str, str]]:
    body = markdown_text.section(template_binding(), ENTRY_POINTS_SECTION)
    rows = dict(markdown_text.table_cells(body))
    return {
        match.group(1): (line, match.group(2))
        for line in body.splitlines()
        if (match := markdown_text.TABLE_ROW.match(line)) and match.group(1) in rows
    }


@functools.cache
def template_gate_row(question: str) -> str:
    body = markdown_text.section(template_binding(), "## Gates")
    prefix = f"| {question} |"
    return next(line for line in body.splitlines() if line.startswith(prefix))


def entry_points(text: str) -> dict[str, str]:
    return dict(markdown_text.table_cells(markdown_text.section(text, ENTRY_POINTS_SECTION)))


def entry_row(name: str, rows: dict[str, tuple[str, str]], *, on_just: bool) -> Finding:
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


@functools.cache
def missing_recipe(
    justfile: str, recipe: str, name: str, *, needed: str = "", placeholder: bool = False
) -> Finding:
    text = template_file("justfile")
    start = justfile_text.definition(text, recipe)
    needed = needed or f"the binding's entry point '{name}' runs"
    problem = f"has no `{recipe}` recipe, which {needed}; copy it from the template justfile"
    reasons = justfile_text.unmet(text, justfile, recipe)
    if placeholder:
        reasons = tuple(reason for reason in reasons if justfile_text.BINDS not in reason)
        problem += "; it is a placeholder for the project to bind"
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
            yield missing_recipe(
                justfile, recipe.group(1), name, placeholder=name == comments.LISTER
            )


def check_binding(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None:
        yield agent(f"{BINDING_PATH}: missing")
        return
    yield from check_entry_points(root, text)
    if not markdown_text.has_section(text, TESTS_SECTION):
        problem = (
            f"{BINDING_PATH}: has no '{TESTS_SECTION}' section, where the test runner and the"
            " directories tests live in are named"
        )
        yield missing_section(
            BINDING_PATH, problem, TESTS_SECTION, template_text=template_binding()
        )
    paths, problems = change_size.test_paths(text)
    if paths is None and problems[0].startswith("has no"):
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
    adrs = {number for number, _ in numbered_files(root / "docs/adr")}
    catalogue = application_types.load()
    rows = markdown_text.table_cells(gates)
    dropped = {
        application_types.dropped(question): question
        for question in sorted(catalogue.questions - {question for question, _ in rows})
    }
    typing = catalogue.types_problem(text)
    fired = Fired.of(root, text) if dropped else Fired({}, frozenset())
    moved = moved_triggers(catalogue, rows)
    for failure in unfolded(catalogue.failures(text, rows, adrs), dropped, text):
        question = dropped.get(failure)
        if question is not None:
            trigger = catalogue.trigger_of(question)
            yield dropped_row(failure, question, trigger=trigger, fired=fired)
        elif failure in moved:
            question, trigger = moved[failure]
            does = f"write the '{question}' row as `later: {trigger}`"
            want = markdown_text.with_cell(
                "## Gates", question, column=1, value=f"later: {trigger}"
            )
            yield Finding(BINDING_PATH, failure, Edit(does, want, Agent()))
        elif failure == typing:
            yield agent(f"{BINDING_PATH}: {failure}", then=triggers_follow(catalogue))
        else:
            yield agent(f"{BINDING_PATH}: {failure}")
    yield from library_only(text, rows)


LIBRARY_ONLY = "documentation coverage, ratcheted"


def library_only(binding: str, rows: Sequence[tuple[str, str]]) -> Iterable[Finding]:
    kinds = application_types.declared(binding)
    if kinds is None or comments.LIBRARY in kinds:
        return
    for question, tool in rows:
        if question == LIBRARY_ONLY and bound(tool):
            problem = (
                f"gate '{question}' is asked of a library only, and the types do not declare"
                f" one; write `{comments.NOT_A_LIBRARY}` (ADR 0063: the owner merges this"
                " unbinding)"
            )
            want = markdown_text.with_cell(
                "## Gates", question, column=1, value=comments.NOT_A_LIBRARY
            )
            does = f"write `{comments.NOT_A_LIBRARY}` in the '{question}' row"
            yield Finding(BINDING_PATH, problem, Edit(does, want, Agent()))


def check_architecture(root: Path) -> Iterable[Finding]:
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
    for problem in [*declared.problems, *comments.interface_problems(text)]:
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
    found: dict[str, list[str]]
    events: frozenset[str]

    @classmethod
    def of(cls, root: Path, binding: str) -> Fired:
        kinds, _ = triggers.parse(binding)
        return cls(triggers.instances(root, None, kinds), application_types.life_events(binding))

    def part(self, part: str) -> tuple[bool | None, str]:
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


def moved_triggers(
    catalogue: application_types.Catalogue, rows: Sequence[tuple[str, str]]
) -> dict[str, tuple[str, str]]:
    moved = {}
    for question, tool in rows:
        waits = application_types.LATER.match(tool)
        if waits is None or question not in catalogue.questions:
            continue
        trigger = catalogue.trigger_of(question)
        failure = catalogue.later_problem(question, waits.group(1).strip())
        if failure is not None and trigger != application_types.ALWAYS:
            moved[failure] = (question, trigger)
    return moved


def dropped_row(failure: str, question: str, *, trigger: str, fired: Fired) -> Finding:
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
        if shipped(row) or gate in CARRIED_GATES:
            edit = (f"add the '{question}' row, bound to the plugin's own gate", row)
    elif {state for state, _ in said} == {True}:
        fix = Agent(start, (*shown, bite))
        if FORMAL_PROOF in application_types.load().proofs.get(question, ()):
            fill = (
                f"add the template's row as `later: {trigger}`: it has fired here, so the row is"
                " owed"
            )
            edit = (
                f"add the '{question}' row as `later: {trigger}`",
                with_tool(row, f"later: {trigger}"),
            )
        else:
            fill = f"add the template's row and bind it: its trigger `{trigger}` has fired here"
    else:
        fill = (
            f"add the template's row, bound, or `later: {trigger}` only while that trigger has"
            " not fired"
        )
        waits = f"; a row that says `later: {trigger}` needs none while that trigger has not fired"
        fix = Agent(start, (*shown, bite + waits))
        if None not in {state for state, _ in said}:
            edit = (
                f"add the '{question}' row as `later: {trigger}`",
                with_tool(row, f"later: {trigger}"),
            )
    problem = f"{failure}; {fill}"
    if edit is None:
        return Finding(BINDING_PATH, problem, fix, identity=failure, stops=question)
    does, written = edit
    want = markdown_text.with_row("## Gates", written, template_questions())
    return Finding(BINDING_PATH, problem, Edit(does, want, fix), identity=failure, stops=question)


def shipped(row: str) -> bool:
    return project_bite.SHIPPED.fullmatch(row.split("|")[2].strip()) is not None


def with_tool(row: str, tool: str) -> str:
    cells = row.split("|")
    cells[2] = f" {tool} "
    return "|".join(cells)


@functools.cache
def template_questions() -> tuple[str, ...]:
    return tuple(row.question for row in project_bite.gate_rows(template_binding()))


def triggers_follow(catalogue: application_types.Catalogue) -> tuple[str, ...]:
    return tuple(
        f"`{name}` asks the Triggers table for the kinds {', '.join(sorted(kinds))}"
        for name in catalogue.types
        if (kinds := catalogue.kinds_required({name}))
    )


def life_events_question(root: Path) -> Owner:
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


def check_trigger_kinds(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None or not markdown_text.has_section(text, triggers.SECTION.strip()):
        return
    catalogue = application_types.load()
    kinds, _ = triggers.parse(text)
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


FORMAL_PROOF = "code-departs-from-model"
FORMAL_STOPS = "the proof that the formal model's code is the code it mirrors"


def default_ref(root: Path) -> str | None:
    return next(
        (
            name
            for name in gatelib.default_branches(root)
            if gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
        ),
        None,
    )


def formal_globs(root: Path, formal: formal_model.Formal) -> Iterable[Finding]:
    tracked = gatelib.tracked_paths(root)
    for label, globs in (("Model", formal.model), ("Mirrors", formal.mirrors)):
        for glob in globs:
            if not any(gatelib.matches(path, [glob]) for path in tracked):
                yield agent(
                    f"{BINDING_PATH}: the '- {label}:' glob `{glob}` of '{formal_model.SECTION}'"
                    " matches no tracked file; write the glob of a file that exists"
                )


def formal_group(root: Path, formal: formal_model.Formal) -> Iterable[Finding]:
    ref = default_ref(root)
    text = gatelib.show(root, ref, "guards/manifest.toml") if ref else None
    try:
        guards = guard_manifest.parse(text if text is not None else "")
    except guard_manifest.ManifestError:
        return
    for path in gatelib.tracked_paths(root):
        group = guards.group_of(path)
        if gatelib.matches(path, list(formal.model)) and (group is None or group.name != "formal"):
            yield agent(
                f"guards/manifest.toml: `{path}` is a '- Model:' path of '{formal_model.SECTION}'"
                f" and the default branch's `formal` group does not hold it; add it there, so the"
                " owner merges a change to the model"
            )


def formal_synced(root: Path, formal: formal_model.Formal) -> Iterable[Finding]:
    synced = formal.synced
    if not synced:
        yield agent(f"{BINDING_PATH}: '{formal_model.SECTION}' has no '- Synced:' commit")
    elif gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{synced}^{{commit}}") is None:
        yield agent(
            f"{BINDING_PATH}: the '- Synced:' commit {synced} is not in this clone; fetch it with"
            f" `git fetch origin {synced}`, or the full history with `git fetch --unshallow`"
        )
    elif gatelib.git(root, "merge-base", "--is-ancestor", synced, "HEAD") is None:
        yield agent(f"{BINDING_PATH}: the '- Synced:' commit {synced} is not an ancestor of HEAD")


def inside(planting: set[str], formal: formal_model.Formal, paths: Sequence[str]) -> bool:
    return bool(planting) and all(
        gatelib.matches(path, list(formal.mirrors))
        and gatelib.matches(path, list(paths))
        and not gatelib.matches(path, list(formal.model))
        for path in planting
    )


def formal_bites(
    root: Path, binding: str, formal: formal_model.Formal, *, rows: Sequence[project_bite.Row]
) -> Iterable[Finding]:
    catalogue = application_types.load()
    kinds, _ = triggers.parse(binding)
    source = read(root / project_bite.REGISTRY)
    bites = project_bite.read_registry(source)[0].bites if source is not None else ()
    for row in rows:
        kind = application_types.code_kinds(catalogue.trigger_of(row.question))[0]
        paths = kinds[kind].paths if kind in kinds else ()
        wanted = [
            bite
            for bite in bites
            if bite.gate == row.gate
            and bite.proof == FORMAL_PROOF
            and inside(planted(root, bite.fault), formal, paths)
        ]
        if not wanted:
            yield replace(
                agent(
                    f"{project_bite.REGISTRY}: the bound row '{row.question}' needs a [[bite]] of"
                    f" gate '{row.gate}' with proof = \"{FORMAL_PROOF}\" whose fault plants only"
                    f" into files matching both a '- Mirrors:' glob and a Paths glob of the"
                    f" `{kind}` Triggers row, and no '- Model:' glob"
                ),
                stops=FORMAL_STOPS,
            )


def check_formal_verification(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None:
        return
    catalogue = application_types.load()
    rows = [
        row
        for row in project_bite.gate_rows(text)
        if FORMAL_PROOF in catalogue.proofs.get(row.question, ()) and row.bound
    ]
    formal = formal_model.read(text)
    if formal is None:
        if rows and not markdown_text.has_section(text, formal_model.SECTION):
            problem = (
                f"has no '{formal_model.SECTION}' section, which says what the formal model"
                " mirrors (ADR 0062); add the template's section and fill it"
            )
            yield missing_section(
                BINDING_PATH, problem, formal_model.SECTION, template_text=template_binding()
            )
        elif rows:
            yield agent(
                f"{BINDING_PATH}: '{formal_model.SECTION}' still holds the template's placeholders;"
                " write '- Model:', '- Mirrors:' and '- Synced:'"
            )
        return
    yield from formal_globs(root, formal)
    yield from formal_group(root, formal)
    yield from formal_synced(root, formal)
    yield from formal_bites(root, text, formal, rows=rows)


def later(tool: str) -> bool:
    return application_types.LATER.match(tool) is not None


DEPENDENCY_STOPS = (
    "the dependency-only judgement of guard-diff and the merge judge, which sends every"
    " dependency change to the owner until the section is filled"
)
DEPENDENCY_RECIPE = "dependencies"


def check_dependencies(root: Path) -> Iterable[Finding]:
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
    text = read(root / "guards/manifest.toml")
    if text is None:
        return
    try:
        guards = guard_manifest.parse(text)
    except guard_manifest.ManifestError:
        return
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
    return bool(tool) and not later(tool) and application_types.NONE.match(tool) is None


SUPPRESSIONS_STOPS = (
    "the suppressions gate and the coverage gate's search for exclusions on absolute paths,"
    " which count nothing until the section is filled"
)


def check_suppressions(root: Path) -> Iterable[Finding]:
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


COMMENTS_STOPS = "the comments gate, which judges nothing until its lister is bound"
COMMENTS_QUESTION = "no prose comments"


def check_comments_section(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None:
        return
    found = comments.table(text)
    if found is None:
        problem = (
            f"has no '{comments.SECTION}' section, which names the lister the `comments` gate"
            " reads (ADR 0063); add the template's section and fill it"
        )
        added = missing_section(
            BINDING_PATH, problem, comments.SECTION, template_text=template_binding()
        )
        yield replace(added, stops=COMMENTS_STOPS)
        return
    for problem in found.problems:
        yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=COMMENTS_STOPS)
    if found.rows:
        for path in comments.tracked_text(root):
            if comments.row_of(found, path) is None:
                yield agent(
                    f"{BINDING_PATH}: '{comments.SECTION}' has no row matching `{path}`; add a"
                    " row for its kind, `listed` or `none: <reason>`"
                )
    listed = [row for row in found.rows if row.listed]
    if listed and comments.placeholder(read_justfile(root)):
        problem = f"the `{comments.LISTER}` recipe still holds the template's `bind:` placeholder"
        yield replace(agent(f"justfile: {problem}"), stops=COMMENTS_STOPS)
    yield from unplanted_rows(root, text, listed)


def check_documents(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None:
        return
    found = documents.declared(text)
    if found is None:
        problem = (
            f"has no '{documents.SECTION}' section, which gives this project's own Markdown files"
            " a kind (ADR 0064); add the template's section and fill it"
        )
        yield missing_section(
            BINDING_PATH, problem, documents.SECTION, template_text=template_binding()
        )
        return
    for problem in found.problems:
        yield agent(f"{BINDING_PATH}: {problem}")
    kinds = documents.all_kinds(text)
    for path in documents.unkinded(gatelib.tracked_paths(root), kinds):
        yield agent(
            f"{BINDING_PATH}: `{path}` is a Markdown file of no kind; add a row for it to"
            f" '{documents.SECTION}', `never` or `pass`, or move it under a kind's paths (ADR 0064)"
        )


def planted(root: Path, fault: project_bite.Fault) -> set[str]:
    if isinstance(fault, project_bite.Write):
        return {fault.path}
    if isinstance(fault, project_bite.Replace):
        return {swap.path for swap in fault.swaps}
    if isinstance(fault, project_bite.Patch):
        diff = root / fault.path
        return project_bite.patch_paths(root, diff.read_bytes() if diff.is_file() else None)
    return set()


def unplanted_rows(root: Path, binding: str, listed: Sequence[comments.Row]) -> Iterable[Finding]:
    gate = next(
        (row for row in project_bite.gate_rows(binding) if row.question == COMMENTS_QUESTION), None
    )
    source = read(root / project_bite.REGISTRY)
    if gate is None or not gate.bound or source is None or not listed:
        return
    registry, _ = project_bite.read_registry(source)
    paths = {
        path
        for bite in registry.bites
        if bite.gate == gate.gate
        for path in planted(root, bite.fault)
    }
    for row in listed:
        if not any(gatelib.matches(path, list(row.globs)) for path in paths):
            globs = ", ".join(f"`{glob}`" for glob in row.globs)
            yield agent(
                f"{project_bite.REGISTRY}: the Comments row {globs} is `listed`, and no [[bite]] of"
                f" gate '{gate.gate}' plants into a file it matches; add one, so the lister is"
                " shown to find a comment of that kind (ADR 0063)"
            )


SIGNS_STOPS = "the route's proposal of a life event it sees a sign of, which waits for the table"
PINS_STOPS = "the pins gate's reading of the project's image files, which reads none until filled"
MUTATION_STOPS = "the mutation gate, which reads no tool's own report any more (ADR 0050)"
MUTATION_SCRIPT = "mutation_survivors.py"
MUTATION_INPUTS = ("--mutants", "--changed")


def check_signs(root: Path) -> Iterable[Finding]:
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


def check_shared_reports(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None:
        return
    reports, problems = report.parse(text)
    if reports is None and problems[0] == f"has no '{report.SECTION}' section":
        problem = (
            f"{problems[0]}; add the template's, bind `suite-report` first to run the suite once,"
            " then bind the `test`, `coverage`, `diff-coverage` and `test-counts` gates as"
            " readers of the shared suite report (ADR 0071)"
        )
        yield missing_section(
            BINDING_PATH, problem, report.SECTION, template_text=template_binding()
        )
        return
    for problem in problems:
        yield agent(f"{BINDING_PATH}: {problem}")
    for shared in reports or []:
        yield from unignored_paths(root, shared)
    justfile = read_justfile(root)
    if justfile is None:
        return
    for name, recipe in report.producers(text):
        yield from producer_recipe(justfile, name, recipe)
    for shared in reports or []:
        yield from shared_report_recipes(justfile, shared)


def producer_recipe(justfile: str, name: str, recipe: str) -> Iterable[Finding]:
    if justfile_text.defines_recipe(justfile, recipe):
        return
    template = template_file("justfile")
    if justfile_text.defines_recipe(template, recipe):
        needed = f"the shared report '{name}' names"
        yield missing_recipe(justfile, recipe, name, needed=needed, placeholder=True)
        return
    yield agent(
        f"justfile: the shared report '{name}' names the recipe '{recipe}',"
        " which the justfile does not define"
    )


def unignored_paths(root: Path, shared: report.Report) -> Iterable[Finding]:
    for path in shared.paths:
        ignored = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", path],
            capture_output=True,
            check=False,
            env={k: v for k, v in os.environ.items() if k not in gatelib.REPOSITORY_VARIABLES},
        )
        if ignored.returncode == 1:
            yield agent(
                f".gitignore: the shared report '{shared.name}' writes '{path}', which git does"
                " not ignore; a report that is tracked or untracked changes the tree's content,"
                " so it is never reused"
            )


def outside_path_argument(line: str, path: str) -> str:
    if report.SCRIPT not in line:
        return line
    argument = re.compile(rf"--path[ =]+(['\"]?){re.escape(path)}\1(?![\w./-])")
    return argument.sub("", line)


def recipe_lines(justfile: str, recipe: str) -> list[str]:
    body = justfile_text.code_of("\n".join(justfile_text.definition(justfile, recipe)))
    return [line for line in body.replace("\\\n", " ").splitlines() if line[:1] in " \t"]


def shared_report_recipes(justfile: str, shared: report.Report) -> Iterable[Finding]:
    for command in shared.readers:
        name = report.recipe_of(command)
        if not justfile_text.defines_recipe(justfile, name):
            yield agent(
                f"justfile: the shared report '{shared.name}' names the recipe '{name}',"
                " which the justfile does not define"
            )
    for command in shared.readers:
        name = report.recipe_of(command)
        for line in recipe_lines(justfile, name):
            named = [path for path in shared.paths if path in outside_path_argument(line, path)]
            if named:
                yield agent(
                    f"justfile: the recipe '{name}' reads the shared report '{shared.name}'"
                    f" ({', '.join(named)}) other than as the --path argument of {report.SCRIPT}:"
                    f" {line.strip()}"
                )


def check_changed_tests(root: Path) -> Iterable[Finding]:
    text = read(root / BINDING_PATH)
    if text is None or not markdown_text.has_section(text, suites.SECTION):
        return
    found, problems = suites.parse(text)
    if suites_table_owed(text):
        problem = (
            "'## Tests' has no suites table (Suite | Gate | Files | Run files). Write it in one"
            " fill with two other edits, since a Gates row bound before the table exists turns"
            " tier 1 red (ADR 0073): the table, one row per suite naming the gate that runs it"
            " whole, the globs of its test files and the recipe that runs given files of it, or"
            " `none: <reason>`; the Gates row 'changed tests run', bound to `just tests-changed`;"
            " and `tests-changed` in the justfile's `tier1_gates`."
        )
        template = markdown_text.section(template_binding(), suites.SECTION)
        start = tuple(line for line in template.splitlines() if line.startswith("|"))
        yield Finding(BINDING_PATH, problem, Agent(start=start), stops=CHANGED_TESTS_STOPS)
    else:
        for problem in problems:
            yield replace(agent(f"{BINDING_PATH}: {problem}"), stops=CHANGED_TESTS_STOPS)
    justfile = read_justfile(root)
    if justfile is not None and not justfile_text.defines_recipe(justfile, "tests-changed"):
        needed = "the method's 'changed tests run' gate runs (ADR 0073)"
        yield missing_recipe(justfile, "tests-changed", CHANGED_TESTS_STOPS, needed=needed)
    for suite in found or []:
        yield from suite_findings(text, justfile, suite)
    yield from clashing_suites(root, found or [])
    yield from later_tier_setup(root, text, found or [])


def unfolded(failures: Iterable[str], dropped: dict[str, str], binding: str) -> Iterable[str]:
    folded = suites_table_owed(binding)
    return (f for f in failures if not (folded and dropped.get(f) == CHANGED_TESTS_STOPS))


def suites_table_owed(binding: str) -> bool:
    found, problems = suites.parse(binding)
    return found is None and problems[0].startswith(f"'{suites.SECTION}' lacks")


def suite_findings(binding: str, justfile: str | None, suite: suites.Suite) -> Iterable[Finding]:
    held = {row.command for row in project_bite.gate_rows(binding)}
    if suite.gate not in held:
        yield agent(
            f"{BINDING_PATH}: the suites row '{suite.name}' names `{suite.gate}`, which no row of"
            " the Gates table holds as its entry point"
        )
    if not suite.run or justfile is None:
        return
    recipe = suite.run.split()[1]
    if not justfile_text.defines_recipe(justfile, recipe):
        yield agent(
            f"justfile: the suites row '{suite.name}' runs given files with the recipe '{recipe}',"
            " which the justfile does not define"
        )
        return
    definition = justfile_text.definition(justfile, recipe)
    head = next(line for line in definition if line[:1] not in "#[")
    if not VARIADIC.search(head.partition(":")[0]):
        yield Finding(
            "justfile",
            f"the recipe '{recipe}' runs given files of the suite '{suite.name}' and takes none;"
            " give it a variadic parameter, such as `*files`, and run only the files it is given,"
            " in the environment and with the build the whole suite has (ADR 0073)",
            Agent(start=definition),
            stops=CHANGED_TESTS_STOPS,
        )


def clashing_suites(root: Path, found: list[suites.Suite]) -> Iterable[Finding]:
    clashes: dict[tuple[str, ...], list[str]] = {}
    for path in gatelib.tracked_paths(root):
        owners = tuple(suite.name for suite in suites.owners(found, path))
        if len(owners) > 1:
            clashes.setdefault(owners, []).append(path)
    for owners, paths in sorted(clashes.items()):
        names = ", ".join(f"'{name}'" for name in owners)
        shown = ", ".join(paths[:SHOWN]) + (
            f" and {len(paths) - SHOWN} more" if len(paths) > SHOWN else ""
        )
        yield Finding(
            BINDING_PATH,
            f"{shown} match the suites {names}; a test file belongs to one suite, so narrow the"
            " globs of one row",
            Agent(),
            identity=f"the suites {names} claim one file",
            stops=CHANGED_TESTS_STOPS,
        )


def later_tier_setup(root: Path, binding: str, found: list[suites.Suite]) -> Iterable[Finding]:
    workflow = read(root / WORKFLOWS / "ci.yml")
    rows = project_bite.gate_rows(binding)
    if workflow is None or "tests-changed" in workflow:
        return
    if any(changed_tests.later(rows, suite) for suite in found):
        yield agent(
            f"{WORKFLOWS / 'ci.yml'}: a suite of the suites table has its gate in a later tier,"
            " and no step of the job that runs `tests-changed` sets up what it needs; add the"
            " steps under `if: matrix.gate == 'tests-changed'`, as `deep.yml` sets up the whole"
            f" suite, and give `tests-changed` a job of its own in the justfile's `{ci_jobs.JOBS}`,"
            " since a job of several gates skips a step set up for one"
        )


def check_mutation(root: Path) -> Iterable[Finding]:
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


CHANGED_CAPTURE = re.compile(
    r"\b(?P<var>\w+)=[\"']?\$\((?P<body>[^()]*"
    + re.escape(MUTATION_SCRIPT)
    + r"[^()]*--changed[^()]*)\)"
)


def unfiltered_changed(line: str, later: str = "") -> bool:
    for found in CHANGED_CAPTURE.finditer(line):
        after = f"{line[found.end() :]}\n{later}".replace("||", "")
        reassigned = re.search(rf"\b{found['var']}=", after)
        if "|" not in found["body"] and "|" not in after and "<<<" not in after and not reassigned:
            return True
    return False


def shebang_rest(lines: list[str], at: int) -> str:
    start = at
    while start > 0 and (lines[start - 1][:1].isspace() or not lines[start - 1].strip()):
        start -= 1
    body = lines[start:]
    first = next((line for line in body if line.strip()), "")
    if not first.strip().startswith("#!") or not first[:1].isspace():
        return ""
    end = at + 1
    while end < len(lines) and (lines[end][:1].isspace() or not lines[end].strip()):
        end += 1
    return "\n".join(lines[at + 1 : end])


def check_mutation_filter(root: Path) -> Iterable[Finding]:
    for path in justfile_paths(root):
        lines = (read(path) or "").replace("\\\n", " ").splitlines()
        for at, line in enumerate(lines):
            if not line.lstrip().startswith("#") and unfiltered_changed(
                line, shebang_rest(lines, at)
            ):
                name = path.relative_to(root).as_posix()
                problem = (
                    f"a recipe hands the output of {MUTATION_SCRIPT} --changed to its tool"
                    " unfiltered, and that lists every changed file, so a change with no code in"
                    " it fails the tool; filter the list in the recipe to the files the tool"
                    " mutates, or move to the `--mutants` form (ADR 0050)"
                )
                yield agent(f"{name}: {problem}")
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
        yield from workflow_speed_findings(root, path)
    for path in sorted(workflows.glob("*.yaml")):
        yield from matrix_cache_finding(root, path, path.read_text(encoding="utf-8"))


def workflow_speed_findings(root: Path, path: Path) -> Iterable[Finding]:
    content = path.read_text(encoding="utf-8")
    for marker, meaning in CI_SHAPE:
        builds = path.name != MERGE_WORKFLOW or marker != "cache"
        if builds and marker not in content:
            yield agent(f"{path.relative_to(root)}: nothing {meaning} ('{marker}' absent)")
    yield from tier_step_findings(root, path, content)
    yield from ci_loop_findings(root, path, content)
    regroupable_ci = (
        path.name == "ci.yml"
        and JOB_LIST_SHAPE[0] not in content
        and not isinstance(regroup(content), Kept)
    )
    if not regroupable_ci:
        for name, marker, meaning in CEILING_SHAPE:
            if path.name == name and marker not in content:
                problem = f"nothing {meaning} ('{marker}' absent)"
                does = (
                    "pass the template's budget flags to each budget call an older template wrote"
                )
                yield Finding(
                    str(path.relative_to(root)), problem, Edit(does, budget_flags(name), Agent())
                )
        if path.name == "ci.yml" and "--over report" in content and OLD_OVER_FLAG in content:
            problem = f"still passes `{OLD_OVER_FLAG}` on a tier 0 or tier 1 budget call"
            does = "pass `--over report` to every tier 0 and tier 1 budget call and drop `OVER:`"
            yield Finding(
                str(path.relative_to(root)), problem, Edit(does, budget_flags(path.name), Agent())
            )
        if path.name in TIER_WORKFLOWS:
            yield from timing_cache_findings(root, path, content)
    yield from bare_cache_finding(root, path, content)
    yield from matrix_cache_finding(root, path, content)
    if path.name in TIER_WORKFLOWS and SHARD_MATRIX not in content:
        yield agent(
            f"{path.relative_to(root)}: no long gate runs over the binding's shard count "
            f"('{SHARD_MATRIX}' absent)"
        )
    if path.name == "deep.yml":
        yield from deep_shard_findings(root, path, content)


def deep_shard_findings(root: Path, path: Path, content: str) -> Iterable[Finding]:
    rel = str(path.relative_to(root))
    justfile = read_justfile(root) or ""
    sharded = set(justfile_text.ordered(justfile, "tier2_sharded") or ())
    tier2_long = next(
        (lines for job, lines in workflow_job_blocks(content) if job == "tier2-long"),
        None,
    )
    if tier2_long is None:
        if "bite-all" in sharded:
            yield agent(
                f"{rel}: `tier2_sharded` lists `bite-all`, but no `tier2-long` job runs each slice"
            )
        return
    body = "\n".join(tier2_long)
    missing = [why for marker, why in DEEP_SLICE_REQUIREMENTS if marker not in body]
    if missing:
        yield agent(f"{rel}: job `tier2-long` does not cover every slice ({', '.join(missing)})")


def strip_yaml_comment(text: str) -> str:
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or text[index - 1].isspace()):
            return text[:index].rstrip()
    return text.rstrip()


def unquote_yaml(raw: str) -> str:
    stripped = strip_yaml_comment(raw).strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1]
    return stripped


def has_matrix_expr(value: str) -> bool:
    return any(MATRIX_EXPR.search(expr) for expr in GHA_EXPR.findall(value))


def workflow_job_blocks(content: str) -> list[tuple[str, list[str]]]:
    lines = content.splitlines()
    jobs_at = next((i for i, line in enumerate(lines) if strip_yaml_comment(line) == "jobs:"), None)
    if jobs_at is None:
        return []
    found: list[tuple[str, list[str]]] = []
    job_indent: int | None = None
    for line in lines[jobs_at + 1 :]:
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent == 0:
            break
        if job_indent is None:
            job_indent = indent
        if indent == job_indent and (match := YAML_JOB_KEY.match(stripped)):
            found.append((match.group(1), []))
        elif found and indent > job_indent:
            found[-1][1].append(line)
    return found


def mapping_section(lines: list[str], key: str) -> tuple[str, list[str]] | None:
    if not lines:
        return None
    base_indent = min(len(line) - len(line.lstrip(" ")) for line in lines)
    collecting, inline = False, ""
    body: list[str] = []
    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if indent == base_indent:
            if collecting:
                return inline, body
            cleaned = strip_yaml_comment(stripped)
            if cleaned == f"{key}:" or cleaned.startswith(f"{key}: "):
                collecting = True
                inline = cleaned.partition(":")[2].strip()
        elif collecting and indent > base_indent:
            body.append(line)
    return (inline, body) if collecting else None


def job_has_matrix(job_lines: list[str]) -> bool:
    section = mapping_section(job_lines, "strategy")
    if section is None:
        return False
    inline, body = section
    if inline.startswith("{") and FLOW_MATRIX.search(inline):
        return True
    return mapping_section(body, "matrix") is not None


def step_items(lines: list[str]) -> list[list[str]]:
    if not lines:
        return []
    step_indent = len(lines[0]) - len(lines[0].lstrip(" "))
    items: list[list[str]] = []
    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if indent == step_indent and stripped.startswith("- "):
            lead = len(stripped) - len(stripped[1:].lstrip(" "))
            items.append([f"{' ' * (step_indent + lead)}{stripped[lead:]}"])
        elif items and indent > step_indent:
            items[-1].append(line)
    return items


def balanced_flow_item(text: str, start: int) -> str:
    depth, quote = 0, ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return text[start:index].strip()
            depth -= 1
        elif char == "," and depth == 0:
            return text[start:index].strip()
    return text[start:].strip()


def flow_value(text: str, field: str) -> str | None:
    match = re.search(rf"(?:^|[,{{\s]){re.escape(field)}\s*:\s*", text)
    return None if match is None else balanced_flow_item(text, match.end())


def flow_cache_key(text: str) -> tuple[bool, str | None]:
    uses = flow_value(text, "uses")
    if uses is None or not CACHE_ACTION.fullmatch(unquote_yaml(uses)):
        return False, None
    with_block = flow_value(text, "with")
    key = flow_value(with_block, "key") if with_block else None
    return True, (unquote_yaml(key) or None) if key else None


def block_cache_key(step_lines: list[str]) -> tuple[bool, str | None]:
    uses = mapping_section(step_lines, "uses")
    if uses is None or not CACHE_ACTION.fullmatch(unquote_yaml(uses[0])):
        return False, None
    with_sec = mapping_section(step_lines, "with")
    if with_sec is None:
        return True, None
    with_inline, with_body = with_sec
    if with_inline.startswith("{"):
        key = flow_value(with_inline, "key")
        return True, (unquote_yaml(key) or None) if key else None
    key_sec = mapping_section(with_body, "key")
    if key_sec is None:
        return True, None
    key_inline, key_body = key_sec
    if key_inline in MULTILINE_KEY:
        joined = " ".join(strip_yaml_comment(line).strip() for line in key_body).strip()
        return True, joined or None
    return True, unquote_yaml(key_inline) or None


def step_cache_key(step_lines: list[str]) -> tuple[bool, str | None]:
    first = step_lines[0].lstrip(" ")
    return flow_cache_key(first) if first.startswith("{") else block_cache_key(step_lines)


def matrix_cache_finding(root: Path, path: Path, content: str) -> Iterable[Finding]:
    rel = str(path.relative_to(root))
    for job, job_lines in workflow_job_blocks(content):
        if not job_has_matrix(job_lines):
            continue
        steps = mapping_section(job_lines, "steps")
        for step_lines in step_items(steps[1] if steps else []):
            is_cache, key = step_cache_key(step_lines)
            if is_cache and (key is None or not has_matrix_expr(key)):
                what = (
                    f"cache key `{key}` names no matrix value"
                    if key is not None
                    else "its cache step has no `with.key` naming a matrix value"
                )
                yield agent(
                    f"{rel}: job `{job}` defines a matrix, but {what} (include"
                    " `${{ matrix.<key> }}` so matrix jobs do not share one cache key)"
                )


def bare_cache_finding(root: Path, path: Path, content: str) -> Iterable[Finding]:
    if not bare_cache_keys(content):
        return
    problem = (
        "a cache key has no per-run part, so it hits exactly after its first save and "
        "never saves again (add '-${{ github.sha }}')"
    )
    does = "end each cache key with '-${{ github.sha }}', leaving restore-keys as they are"
    then = (
        "scope the cache key of a job that runs once per gate, and its restore-keys prefix,",
        "by '${{ matrix.gate }}', as the template does",
    )
    edit = Edit(does, per_run_keys, Agent(then=then))
    yield Finding(
        str(path.relative_to(root)), problem, edit, identity="cache key without a per-run part"
    )


def cache_key_lines(lines: list[str]) -> list[tuple[int, re.Match[str]]]:
    found: list[tuple[int, re.Match[str]]] = []
    cache = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("- ", "-\t")) and CACHE_USES not in line:
            cache = False
        if CACHE_USES in line:
            cache = True
        elif cache and (match := CACHE_KEY.match(line)):
            found.append((index, match))
    return found


def bare_cache_keys(content: str) -> list[tuple[int, re.Match[str]]]:
    return [
        (index, match)
        for index, match in cache_key_lines(content.split("\n"))
        if not any(part in match.string for part in PER_RUN_PARTS)
    ]


def per_run_keys(current: str | None) -> str:
    lines = (current or "").split("\n")
    for index, match in bare_cache_keys(current or ""):
        value = match["value"]
        quote = value[-1:] if len(value) > 1 and value[0] == value[-1] in "\"'" else ""
        body = value[: len(value) - len(quote)]
        ended = f"{body}-${{{{ github.sha }}}}{quote}"
        lines[index] = f"{match['head']}{ended}{match['note'] or ''}"
    return "\n".join(lines)


def timing_cache_findings(root: Path, path: Path, content: str) -> Iterable[Finding]:
    rel = str(path.relative_to(root))
    for job, job_lines in workflow_job_blocks(content):
        if "budget.py" not in "\n".join(job_lines):
            continue
        steps = mapping_section(job_lines, "steps")
        cache_steps = [
            step_lines
            for step_lines in step_items(steps[1] if steps else [])
            if step_cache_key(step_lines)[0]
        ]
        if cache_steps and not any(TIMING_LOG in "\n".join(step) for step in cache_steps):
            problem = (
                f"job `{job}` does not cache `{TIMING_LOG}`, so `budget --set` has no"
                " runner history"
            )
            does = f"add `{TIMING_LOG}` to each budget job's cache path"
            yield Finding(rel, problem, Edit(does, with_timing_cache(), Agent()))
            return


def with_timing_cache() -> gatelib.Want:
    def want(current: str | None) -> str:
        lines = (current or "").split("\n")
        out: list[str] = []
        in_cache = False
        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.lstrip(" ")
            if stripped.startswith("- "):
                in_cache = CACHE_USES in line
            if in_cache and stripped.startswith("path:"):
                indent = line[: len(line) - len(stripped)]
                value = stripped.partition(":")[2].strip()
                if value == "|":
                    out.append(line)
                    index += 1
                    body: list[str] = []
                    while index < len(lines) and (
                        not lines[index].strip()
                        or len(lines[index]) - len(lines[index].lstrip(" ")) > len(indent)
                    ):
                        body.append(lines[index])
                        index += 1
                    if TIMING_LOG not in "\n".join(body) and body:
                        child_indent = body[0][: len(body[0]) - len(body[0].lstrip(" "))]
                        body.append(f"{child_indent}{TIMING_LOG}")
                    out.extend(body)
                    continue
                if value and not value.startswith("[") and TIMING_LOG not in value:
                    out.extend(
                        [f"{indent}path: |", f"{indent}  {value}", f"{indent}  {TIMING_LOG}"]
                    )
                    index += 1
                    continue
            out.append(line)
            index += 1
        return "\n".join(out)

    return want


def budget_flags(workflow: str) -> gatelib.Want:
    tier = "tier2" if workflow == "deep.yml" else "tier1"
    over = " --over report" if workflow == "ci.yml" else ""
    flags = {OLD_TIER0_BUDGET: over, OLD_GATE_BUDGET: f" --tier {tier}{over}"}

    def want(current: str | None) -> str:
        lines = (current or "").split("\n")
        for index, line in enumerate(lines):
            indent, _, run = line.partition("- run: ")
            if not indent.strip() and run in flags and flags[run]:
                lines[index] = (
                    f"{indent}- run: {run.replace(' --report ', f'{flags[run]} --report ', 1)}"
                )
            elif (
                not indent.strip()
                and workflow == "ci.yml"
                and "budget.py" in run
                and OLD_OVER_FLAG in run
            ):
                lines[index] = f"{indent}- run: {run.replace(OLD_OVER_FLAG, '--over report')}"
        if workflow == "ci.yml" and OLD_OVER_FLAG not in "\n".join(lines):
            lines = [line for line in lines if line != OLD_OVER_ENV]
        return "\n".join(lines)

    return want


def with_shards(text: str | None) -> str:
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
        elif part == "Incremental" and (not value or value.startswith("<")):
            yield agent(
                f"{BINDING_PATH}: the Speed section has no '- Incremental:' line naming the"
                " mutation tool's incremental mode, or `none: <reason>` where the tool has none"
            )
        elif part == "Incremental" and not NONE_WITH_REASON.fullmatch(value):
            if value.startswith("none"):
                yield agent(
                    f"{BINDING_PATH}: the Speed section's '- Incremental:' line says `none` with"
                    " no reason: write `none: <reason>`, or name the mode"
                )
        elif not value or value.startswith("<"):
            yield agent(f"{BINDING_PATH}: the Speed section does not say how '{part}' is met")


def check_local_caches(root: Path) -> Iterable[Finding]:
    body = markdown_text.section(read(root / BINDING_PATH) or "", SPEED_SECTION)
    value = markdown_text.bullets(body).get(runrecord.LOCAL_CACHES)
    if value is None or value.startswith("<"):
        return
    spans = markdown_text.ticked(value)
    if not spans or any(" " in span for span in spans):
        yield agent(
            f"{BINDING_PATH}: the Local caches bullet of the Speed section is not a list of paths,"
            " each as a code span with no space in it",
            then=("name each path the tools write locally as a code span, or take the bullet out",),
        )


INCREMENTAL_VARIABLE = "mutation_incremental"
INCREMENTAL_RECIPE = "mutation-changed"
CI_SWITCH = ('env_var_or_default("CI"', 'env_var_or_default("RATCHET_BITING"')


def incremental_line(root: Path) -> str | None:
    text = read(root / BINDING_PATH) or ""
    if not markdown_text.has_section(text, SPEED_SECTION):
        return None
    value = dict(SPEED_PART.findall(markdown_text.section(text, SPEED_SECTION))).get("Incremental")
    return value if value and not value.startswith("<") else None


def check_incremental(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    if justfile is None:
        return
    line = incremental_line(root)
    held = justfile_text.variables(justfile).get(INCREMENTAL_VARIABLE)
    if line is not None and NONE_WITH_REASON.fullmatch(line):
        if held is not None and held.strip() not in ('""', "''"):
            yield Finding(
                "justfile",
                f"the binding's '- Incremental:' line says `none`, and `{INCREMENTAL_VARIABLE}`"
                ' names a mode; set it to "": a tool with no incremental mode has no option to add',
                Agent(start=template_definition(INCREMENTAL_VARIABLE)),
            )
        return
    if held is None:
        problem = (
            f"has no `{INCREMENTAL_VARIABLE}` variable, which holds the mutation tool's incremental"
            " mode for the recipe `mutation-changed` and is empty in CI and in the bite proof's"
            " copy; add the template's"
        )
        does = f"add the template's `{INCREMENTAL_VARIABLE}`, left for the project to bind"
        want = justfile_text.with_variable(
            INCREMENTAL_VARIABLE,
            lambda _: justfile_text.variables(template_file("justfile"))[INCREMENTAL_VARIABLE],
        )
        fix = Agent(start=template_definition(INCREMENTAL_VARIABLE))
        yield Finding("justfile", problem, Edit(does, want, fix))
        return
    if line is None:
        return
    start = Agent(start=template_definition(INCREMENTAL_VARIABLE))
    if not all(part in held for part in CI_SWITCH):
        yield Finding(
            "justfile",
            f"`{INCREMENTAL_VARIABLE}` is not empty when `CI` or `RATCHET_BITING` is set; CI and"
            " the bite proof's copy both run without the cache, so give it the template's switch,"
            " which is empty when either is set",
            start,
        )
    if justfile_text.BINDS in held:
        yield Finding(
            "justfile",
            f"`{INCREMENTAL_VARIABLE}` still holds the template's `bind:` placeholder; bind it to"
            " the mutation tool's option that the binding's '- Incremental:' line names",
            start,
        )
    body = "\n".join(justfile_text.definition(justfile, INCREMENTAL_RECIPE))
    if body and justfile_text.BINDS not in body and f"{{{{{INCREMENTAL_VARIABLE}}}}}" not in body:
        yield Finding(
            "justfile",
            f"the recipe `{INCREMENTAL_RECIPE}` does not use `{{{{{INCREMENTAL_VARIABLE}}}}}`, so"
            " the binding's '- Incremental:' line is not what it runs; add the variable to the"
            " mutation tool's command",
            Agent(start=justfile_text.definition(justfile, INCREMENTAL_RECIPE)),
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
    yield from deep_record_locked(locked)


def deep_record_locked(locked: set[str]) -> Iterable[Finding]:
    if not gatelib.matches(DEEP_RECORD, sorted(locked)):
        yield owner(
            f"guards/manifest.toml: '{DEEP_RECORD}' is not in an edit = \"deny\" group",
            f"Add '{DEEP_RECORD}' to the `local-git` group, as the template has it? With no forge"
            " the merge reads tier 2 from that record; an agent adds the line on a branch, and"
            " you decide at that merge.",
            assumed=OPEN_RECORD,
        )


def check_default_branch(root: Path) -> Iterable[Finding]:
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
    if not (root / WORKFLOWS / "ci.yml").is_file():
        return
    text = read(root / WORKFLOWS / name)
    if text is None:
        yield whole_file(str(WORKFLOWS / name), f"missing; {unmet}")
        return
    for marker, meaning in shape:
        if marker in text:
            continue
        problem = f"nothing {meaning} ('{marker}' absent)"
        path = str(WORKFLOWS / name)
        template = ADOPT_TEMPLATES / WORKFLOWS / name
        if name == MERGE_WORKFLOW and marker == TIER1_FLAG:
            does = "read the default branch's tier 1 and pass it to the judge, as the template does"
            yield Finding(path, problem, Edit(does, with_tier1(name), Agent()))
        elif name == MERGE_WORKFLOW and marker == KEPT_BRANCH:
            does = "keep a merged branch an open pull request targets, as the template does"
            step = f"replace the step that deletes the merged branch with the one in {template}"
            fix = Edit(does, with_kept_branch(name), Agent())
            yield Finding(path, f"{problem}; {step}", fix, identity=problem)
        elif name == MERGE_WORKFLOW and marker in DEEP_MERGE_MARKERS:
            does = "read every deep run and wake on deep runs, as the template does"
            yield Finding(path, problem, Edit(does, with_deep_merge(name), Agent()))
        else:
            yield agent(f"{path}: {problem}")


def with_tier1(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}").splitlines()
    read_line = next((line for line in template if line.lstrip().startswith("tier1=")), None)

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        at = next(
            (i for i, line in enumerate(lines) if line.lstrip().startswith(("tier2=", "answer="))),
            None,
        )
        deep_flag = next((flag for flag in (TIER2_FLAG, DEEP_ARGUMENT) if flag in text), None)
        call = (
            None
            if deep_flag is None
            else next(
                (i for i, line in enumerate(lines) if "--target" in line and deep_flag in line),
                None,
            )
        )
        if (
            read_line is None
            or TIER1_FLAG in text
            or at is None
            or call is None
            or deep_flag is None
        ):
            return text
        lines[call] = lines[call].replace(deep_flag, f"{TIER1_ARGUMENT} {deep_flag}", 1)
        anchor = lines[at]
        lines[at:at] = [anchor[: len(anchor) - len(anchor.lstrip())] + read_line.lstrip()]
        return "\n".join(lines)

    return want


@functools.cache
def with_deep_merge(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}")
    hunks = (
        (
            "on:\n  workflow_run:\n    workflows: [ci]\n    types: [completed]",
            template[template.index("on:\n") : template.index("\npermissions:\n")],
        ),
        (
            (
                "    if: >-\n"
                "      github.event.workflow_run.event == 'pull_request'\n"
                "      && github.event.workflow_run.conclusion == 'success'\n"
                "      && github.event.workflow_run.path == '.github/workflows/ci.yml'\n"
                "      && github.event.workflow_run.head_repository.full_name == github.repository"
            ),
            template[template.index("    if: >-\n") : template.index("\n    runs-on:")],
        ),
        (
            (
                '          tier2="$(runs deep.yml -f "branch=$DEFAULT_BRANCH" -f status=completed'
                ' | counted deep --tip "origin/$DEFAULT_BRANCH")"'
            ),
            template[
                template.index('          answer="$RUNNER_TEMP/deep.json"\n') : template.index(
                    "\n          gh pr list"
                )
            ],
        ),
        (
            (
                "            verdict=owner\n"
                '            if python3 "$RATCHET_PLUGIN/scripts/merge_ready.py"'
                ' --base "origin/$DEFAULT_BRANCH" --head "$sha" \\\n'
                '              --target "$target" --current --tier1 "$tier1" --tier2 "$tier2"'
                ' --report "$report"; then verdict=agent; fi\n'
                '            if [ ! -s "$report" ]; then'
                ' echo "#$number: the judge wrote no report";'
                " failed=1; continue; fi\n"
                '            comment "$number" "$report"\n'
                '            echo "#$number: merge: $verdict"'
            ),
            template[
                template.index(
                    '            judged="$RUNNER_TEMP/judged-$number.txt"\n'
                ) : template.index('\n            if [ "$verdict" = agent ]; then')
            ],
        ),
        (
            (
                '          if [ "$merged" = 1 ]; then'
                ' gh workflow run deep.yml --ref "$DEFAULT_BRANCH"; fi'
            ),
            template[
                template.index('          if [ "$merged" = 1 ]; then') : template.index(
                    '\n          exit "$failed"'
                )
            ],
        ),
    )

    def want(current: str | None) -> str:
        text = current or ""
        eol = "\r\n" if "\r\n" in text else "\n"
        body = text.replace("\r\n", "\n")
        changed = False
        for old, new in hunks:
            if body.count(new) == 1:
                continue
            if body.count(old) != 1:
                return text
            body = body.replace(old, new, 1)
            changed = True
        return body.replace("\n", eol) if changed else text

    return want


@functools.cache
def with_deep_finish() -> gatelib.Want:
    def want(current: str | None) -> str:
        text = current or ""
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        if lines.count("  cancel-in-progress: true") != 1:
            return text
        at = lines.index("  cancel-in-progress: true")
        above = len(("concurrency:", "  group:"))
        if at < above or lines[at - above] != "concurrency:":
            return text
        lines[at] = "  cancel-in-progress: false"
        return eol.join(lines)

    return want


@functools.cache
def with_deep_shard_check(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}")
    new_shape = template[template.index("\n  shape:\n") + 1 : template.index("\n  tier2:\n") + 1]
    stripped = [
        line.replace('\\nquality/"', '"')
        for line in new_shape.splitlines()
        if "repository: Simon-GitHub-Code/ratchet" not in line
        and DEEP_SHARD_CHECK_MARKER not in line
    ]
    checkout_prefix = "      - uses: actions/checkout@"
    kept = [
        line
        for index, line in enumerate(stripped)
        if not (
            line.startswith(checkout_prefix)
            and index + 1 < len(stripped)
            and stripped[index + 1].lstrip().startswith("- uses:")
        )
    ]
    old_shape = "\n".join(kept) + "\n"

    def want(current: str | None) -> str:
        text = current or ""
        eol = "\r\n" if "\r\n" in text else "\n"
        body = text.replace("\r\n", "\n")
        refs = plugin_pin.refs(text)
        if len(refs) != 1:
            return text
        ref = next(iter(refs))
        before = old_shape.replace(PLUGIN_CHECKOUT_PLACEHOLDER, ref)
        after = new_shape.replace(PLUGIN_CHECKOUT_PLACEHOLDER, ref)
        if body.count(before) != 1:
            return text
        return body.replace(before, after, 1).replace("\n", eol)

    return want


@functools.cache
def with_deep_speaks(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}")
    job = template[template.index("\n  speaks:\n") + 1 :].rstrip("\n")

    def want(current: str | None) -> str:
        text = current or ""
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        if "jobs:" not in lines:
            return text
        heads = job_heads(lines)
        if set(heads.values()) != {"shape", "tier2", "tier2-long"}:
            return text
        after_jobs = lines.index("jobs:") + 1
        if any(line[:1] not in ("", " ", "#") for line in lines[after_jobs:]):
            return text
        refs = plugin_pin.refs(text)
        if len(refs) != 1:
            return text
        added = job.replace(PLUGIN_CHECKOUT_PLACEHOLDER, next(iter(refs)))
        body = "\n".join(lines).rstrip("\n") + "\n" + added
        return (body + ("\n" if text.endswith(("\n", "\r\n")) else "")).replace("\n", eol)

    return want


def with_kept_branch(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}").splitlines()
    start = next(i for i, line in enumerate(template) if KEPT_BRANCH in line) - 1
    stop = next(i for i in range(start, len(template)) if template[i].strip() == "fi ;;") + 1
    block = template[start:stop]
    deleted = next(line.strip() for line in block if "-X DELETE" in line)
    indent = len(block[0]) - len(block[0].lstrip())

    def want(current: str | None) -> str:
        lines = (current or "").split("\n")
        at = next((i for i, line in enumerate(lines) if line.strip() == f"*) {deleted} ;;"), None)
        if at is not None:
            lead = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
            lines[at : at + 1] = [lead + line[indent:] for line in block]
        return "\n".join(lines)

    return want


def ci_loop_findings(root: Path, path: Path, content: str) -> Iterable[Finding]:
    if path.name != "ci.yml" or JOB_LIST_SHAPE[0] not in content:
        return
    name = str(path.relative_to(root))
    if CI_RUN_MARKER not in content:
        problem = (
            f"nothing keeps one run id across every job of the CI run ('{CI_RUN_MARKER}' absent)"
        )
        does = "pass `RATCHET_RUN` to every job of the CI run, as the template does"
        yield Finding(name, problem, Edit(does, with_ci_run(path.name), Agent()))
    if CI_LOOP_MARKER not in content:
        problem = (
            "nothing judges the run's work path against the loop budget"
            f" ('{CI_LOOP_MARKER}' absent)"
        )
        does = "add the `loop` job to judge the run's work path, as the template does"
        yield Finding(name, problem, Edit(does, with_ci_loop(path.name), Agent()))
        return
    blocks = workflow_job_blocks(content)
    loop_lines = next((lines for job, lines in blocks if job == "loop"), None)
    if loop_lines is None:
        yield agent(f"{name}: runs `{CI_LOOP_MARKER}` outside a `loop` job")
        return
    perms = mapping_section(loop_lines, "permissions")
    perm_lines = (
        (perms[0], *(strip_yaml_comment(line).strip() for line in perms[1])) if perms else ()
    )
    if "actions: read" not in perm_lines:
        yield agent(f"{name}: job `loop` needs `permissions: actions: read` to read the run's jobs")
    cond = mapping_section(loop_lines, "if")
    cond_text = f"{cond[0]} {' '.join(cond[1])}" if cond else ""
    if "always()" not in cond_text:
        yield agent(
            f"{name}: job `loop` needs `always()` in `if:` so it runs even when a job fails"
        )
    other_jobs = {job for job, _ in blocks if job != "loop"}
    needs_sec = mapping_section(loop_lines, "needs")
    needs_text = (
        " ".join([needs_sec[0], *(strip_yaml_comment(line) for line in needs_sec[1])])
        if needs_sec
        else ""
    )
    missing = sorted((other_jobs & CORE_CI_JOBS) - set(WORD.findall(needs_text)))
    if missing:
        joined = ", ".join(f"`{job}`" for job in missing)
        yield agent(f"{name}: job `loop` `needs:` omits {joined}")


@functools.cache
def with_ci_run(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}").splitlines()
    run_line = next(line for line in template if CI_RUN_MARKER in line)

    def want(current: str | None) -> str:
        text = current or ""
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        if CI_RUN_MARKER in text or "jobs:" not in lines:
            return text
        jobs_at = lines.index("jobs:")
        if "env:" in lines[:jobs_at]:
            lines[jobs_at:jobs_at] = [run_line]
        else:
            lines[jobs_at:jobs_at] = ["env:", run_line]
        return eol.join(lines)

    return want


@functools.cache
def with_ci_loop(name: str) -> gatelib.Want:
    template = template_file(f".github/workflows/{name}")
    job = template[template.index("\n  loop:\n") + 1 :].rstrip("\n")

    def want(current: str | None) -> str:
        text = current or ""
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        if "jobs:" not in lines or CI_LOOP_MARKER in text:
            return text
        heads = job_heads(lines)
        if set(heads.values()) != CORE_CI_JOBS:
            return text
        after_jobs = lines.index("jobs:") + 1
        if any(line[:1] not in ("", " ", "#") for line in lines[after_jobs:]):
            return text
        refs = plugin_pin.refs(text)
        if len(refs) != 1:
            return text
        added = job.replace(PLUGIN_CHECKOUT_PLACEHOLDER, next(iter(refs)))
        body = "\n".join(lines).rstrip("\n") + "\n" + added
        return (body + ("\n" if text.endswith(("\n", "\r\n")) else "")).replace("\n", eol)

    return want


def tier_step_findings(root: Path, path: Path, content: str) -> Iterable[Finding]:
    for marker, meaning in TIER_STEP_SHAPE.get(path.name, ()):
        name = str(path.relative_to(root))
        problem = f"nothing {meaning} ('{marker}' absent)"
        if marker in content:
            continue
        if marker == DEEP_FINISH_MARKER:
            does = "let a deep run that started finish, as the template does"
            yield Finding(name, problem, Edit(does, with_deep_finish(), Agent()))
        elif marker == DEEP_SHARD_CHECK_MARKER:
            does = "check bite slice partitioning in the shape job, as the template does"
            yield Finding(name, problem, Edit(does, with_deep_shard_check(path.name), Agent()))
        elif marker == DEEP_SPEAKS_MARKER:
            does = (
                "add the job that reports which merges each deep run speaks for,"
                " as the template does"
            )
            yield Finding(name, problem, Edit(does, with_deep_speaks(path.name), Agent()))
        elif marker != JOB_LIST_SHAPE[0]:
            yield agent(f"{name}: {problem}")
        elif ci_jobs.JOBS not in content:
            justfile = read_justfile(root) or ""
            yield regroup_finding(name, problem, content=content, justfile=justfile)


def regroup_finding(name: str, problem: str, *, content: str, justfile: str) -> Finding:
    shaped = regroup(content)
    found = ci_jobs.lists(justfile)
    if isinstance(shaped, Kept):
        why = shaped.reason
    elif not isinstance(found, ci_jobs.Lists) or ci_jobs.refused(found):
        why = f"the justfile has no `{ci_jobs.JOBS}` the shape step would read as its own"
    elif not justfile_text.defines_recipe(justfile, ci_jobs.RECIPE):
        why = f"the justfile has no `{ci_jobs.RECIPE}` recipe to run each job by its name"
    else:
        does = f"run tier 1 as the jobs `{ci_jobs.JOBS}` names, as the template does"
        start = (f"{name} takes the template's change:", *regroup_difference())
        return Finding(name, problem, Edit(does, regrouped, Agent(start=start)))
    lead = f"{name} is kept as it is, because {why}; the template's change, to make by hand:"
    return Finding(name, problem, Agent(start=(lead, *regroup_difference())))


Hunk = tuple[str, tuple[str, ...], tuple[str, ...]]
JOB_HEAD = re.compile(r"^  ([A-Za-z_][\w-]*):\s*$")
JOB_KEY = re.compile(r"^    ([A-Za-z_][\w-]*):")
STEP_ID = re.compile(r"^ +(?:- )?id: ([\w-]+)\s*$")
NEEDS_LINE = re.compile(r"^    needs: (.*)$")
SHAPE_READS = ("needs.shape.outputs.", "matrix.gate")
STEP_BODY = " " * 8
WORD = re.compile(r"[\w-]+")


@dataclass(frozen=True)
class Kept:
    reason: str


@functools.cache
def regroup_hunks() -> tuple[Hunk, ...]:
    lines = template_file(".github/workflows/ci.yml").split("\n")
    at = lines.index("      - id: shards")
    end = next(i for i in range(at + 1, len(lines)) if not lines[i].startswith(STEP_BODY))
    run = next(line for line in lines if line.endswith(NEW_RUN))
    tier0_run = next(line for line in lines if '" tier0 ' in line)
    guard = next(line for line in lines if line.startswith("    if: needs.shape.outputs.tier1 "))
    proven_at = lines.index("  proven:")
    tier0_at = lines.index("  tier0:")
    tier0_runs_at = lines.index("    runs-on: ubuntu-latest", tier0_at)
    timing_at = next(i for i in range(tier0_at, len(lines)) if "timing-tier0" in lines[i])
    shape_at = lines.index("  shape:")
    shape_runs_at = lines.index("    runs-on: ubuntu-latest", shape_at)
    cache_uses = "      - uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6"
    cache_path = ("          path: |", "            .cache")
    cache_path_with_timing = (*cache_path, f"            {TIMING_LOG}")
    cache_step = (
        '      - run: python3 "$RATCHET_PLUGIN/scripts/cache_restore.py"'
        ' --report .ratchet/timing.jsonl --hit "${{ steps.cache.outputs.cache-hit }}" -- .cache'
    )
    run_line = f"  {CI_RUN_MARKER}"
    loop_at = lines.index("  loop:")
    upload_at = next(
        i
        for i in range(lines.index("  tier1-long:"), loop_at)
        if "timing-${{ matrix.gate }}" in lines[i]
    )
    return (
        ("the shape job's steps", OLD_SHAPE_STEP, tuple(lines[at - 2 : end])),
        ("the `tier1:` line", ("  tier1:",), ("  tier1:", guard)),
        ("the tier 1 jobs' `needs:` lines", (OLD_NEEDS,), (NEW_NEEDS,)),
        (
            "the tier 0 budget call's run line",
            (tier0_run.replace("--over report", OLD_OVER_FLAG),),
            (tier0_run,),
        ),
        (
            "the budget calls' run lines",
            (run.removesuffix(NEW_RUN).replace("--over report", OLD_OVER_FLAG) + OLD_RUN,),
            (run,),
        ),
        (
            "the `proven:` job and `tier0:` guard",
            ("  tier0:",),
            tuple(lines[proven_at:tier0_runs_at]),
        ),
        (
            "the `tier0` proven artifact steps",
            tuple(lines[timing_at - 2 : timing_at + 1]),
            tuple(lines[timing_at - 2 : shape_at]),
        ),
        (
            "the `shape:` guard",
            ("  shape:",),
            tuple(lines[shape_at:shape_runs_at]),
        ),
        ("the `actions/cache` step ids", (cache_uses,), (cache_uses, "        id: cache")),
        ("the timing log cache path", cache_path, cache_path_with_timing),
        (
            "the cache restore steps",
            (TOOLCHAIN_PLACEHOLDER,),
            (cache_step, TOOLCHAIN_PLACEHOLDER),
        ),
        ("the `RATCHET_RUN` env var", (OLD_OVER_ENV, "jobs:"), (run_line, "jobs:")),
        (
            "the `loop` job",
            (*lines[upload_at - 2 : upload_at + 1], ""),
            tuple(lines[upload_at - 2 :]),
        ),
    )


@functools.cache
def released() -> tuple[str, ...]:
    old = template_file(".github/workflows/ci.yml").split("\n")
    for _, before, after in reversed(regroup_hunks()):
        for at in reversed(blocks_at(old, after)):
            old[at : at + len(after)] = before
    return tuple(old)


def words_of(blocks: Iterable[str], pattern: re.Pattern[str]) -> set[str]:
    return {word for line in blocks if (m := pattern.match(line)) for word in WORD.findall(m[1])}


@functools.cache
def changed_reads() -> tuple[str, ...]:
    hunks = [hunk for hunk in regroup_hunks() if hunk[0] != "the `loop` job"]
    old = [line for _, before, _ in hunks for line in before]
    new = [line for _, _, after in hunks for line in after]
    gone = {
        f"{kind}.{word}."
        for kind, pattern in (("steps", STEP_ID), ("needs", JOB_HEAD), ("needs", NEEDS_LINE))
        for word in words_of(old, pattern) - words_of(new, pattern)
    }
    return (*SHAPE_READS, *sorted(gone))


def blocks_at(lines: Sequence[str], block: tuple[str, ...]) -> list[int]:
    size = len(block)
    return [at for at in range(len(lines) - size + 1) if tuple(lines[at : at + size]) == block]


def job_heads(lines: Sequence[str]) -> dict[int, str]:
    start = lines.index("jobs:") if "jobs:" in lines else 0
    return {at: m[1] for at in range(start, len(lines)) if (m := JOB_HEAD.match(lines[at]))}


def job_at(heads: dict[int, str], at: int) -> str:
    return next((heads[head] for head in sorted(heads, reverse=True) if head <= at), "")


def job_keys(lines: Sequence[str], heads: dict[int, str], job: str) -> list[str]:
    head = next(at for at, name in heads.items() if name == job)
    keys = []
    for line in lines[head + 1 :]:
        if line.strip() and not line.startswith("    ") and not line.lstrip().startswith("#"):
            break
        keys += [m[1] for m in [JOB_KEY.match(line)] if m]
    return keys


def checkout_line() -> re.Pattern[str]:
    template = next(line for line in regroup_hunks()[0][2] if PLUGIN_CHECKOUT_PLACEHOLDER in line)
    return re.compile(re.escape(template).replace(re.escape(PLUGIN_CHECKOUT_PLACEHOLDER), '[^"]*'))


def without_post_release_budget_edits(lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    if OLD_OVER_ENV not in text and CI_RUN_MARKER not in text and "jobs:" in lines:
        jobs_at = lines.index("jobs:")
        lines = [*lines[:jobs_at], OLD_OVER_ENV, *lines[jobs_at:]]
        text = "\n".join(lines)
    text = text.replace(
        f"          path: |\n            .cache\n            {TIMING_LOG}\n",
        "          path: |\n            .cache\n",
    )
    text = text.replace(
        f" --over report --report {TIMING_LOG} -- just ",
        f" {OLD_OVER_FLAG} --report {TIMING_LOG} -- just ",
    )
    return text.split("\n")


def regroup(text: str) -> str | Kept:
    lines = without_post_release_budget_edits(text.replace("\r\n", "\n").split("\n"))
    checkout = checkout_line()
    plugin = next((line for line in lines if checkout.fullmatch(line)), None)
    if plugin is None:
        return Kept("no line checks the plugin out as the template's does")
    template = released()
    placeholder = next(line for line in template if checkout.fullmatch(line))
    own = [placeholder if checkout.fullmatch(line) else line for line in lines]
    spans = replaced(own, template)
    if isinstance(spans, Kept):
        return spans
    read = changed_read(own, template, spans)
    if read is not None:
        return read
    for at, old, new, _ in sorted(spans, key=lambda span: span[0], reverse=True):
        lines[at : at + len(old)] = [plugin if line == placeholder else line for line in new]
    return "\n".join(lines)


Span = tuple[int, tuple[str, ...], tuple[str, ...], str]


def replaced(own: list[str], template: tuple[str, ...]) -> list[Span] | Kept:
    heads, wrote = job_heads(own), job_heads(template)
    spans: list[Span] = []
    for what, old, new in regroup_hunks():
        found = blocks_at(own, old)
        placed = [job_at(heads, at) for at in found]
        if placed != [job_at(wrote, at) for at in blocks_at(template, old)]:
            return Kept(f"{what} are not as a released template wrote them")
        after = [next((one for one in own[at + len(old) :] if one.strip()), "") for at in found]
        tail_indent = " " * (len(old[-1]) - len(old[-1].lstrip(" ")))
        if old[-1].startswith(STEP_BODY) and any(one.startswith(tail_indent) for one in after):
            return Kept(f"a line the plugin did not write runs on from {what}")
        spans += [(at, old, new, job) for at, job in zip(found, placed, strict=True)]
    for job in dict.fromkeys(job for *_, job in spans if job):
        lacks = set(job_keys(template, wrote, job))
        extra = [key for key in job_keys(own, heads, job) if key not in lacks]
        if extra:
            return Kept(f"the job `{job}` has the key `{extra[0]}`, which the template's job lacks")
    return spans


def changed_read(own: list[str], template: tuple[str, ...], spans: list[Span]) -> Kept | None:
    inside = {at + step for at, old, _, _ in spans for step in range(len(old))}
    matched = difflib.SequenceMatcher(None, template, own, autojunk=False)
    for tag, _, _, first, last in matched.get_opcodes():
        for line in [own[at] for at in range(first, last) if tag != "equal" and at not in inside]:
            read = next((read for read in changed_reads() if read in line), None)
            if read is not None:
                return Kept(
                    f"the line `{line.strip()}`, which the plugin did not write, reads `{read}`,"
                    " which the new shape changes"
                )
    return None


def regrouped(current: str | None) -> str:
    shaped = regroup(current or "")
    return current or "" if isinstance(shaped, Kept) else shaped


@functools.cache
def regroup_difference() -> tuple[str, ...]:
    new = template_file(".github/workflows/ci.yml").split("\n")
    return tuple(
        difflib.unified_diff(
            list(released()),
            new,
            "the previous template's ci.yml",
            "the template's ci.yml",
            n=1,
            lineterm="",
        )
    )


def workflow_markers(name: str) -> tuple[str, ...]:
    shapes = {MERGE_WORKFLOW: MERGE_SHAPE, WEEKLY_WORKFLOW: WEEKLY_SHAPE}
    held = [*(() if name == MERGE_WORKFLOW else CI_SHAPE), *shapes.get(name, ())]
    if name in TIER_WORKFLOWS:
        held += [*TIER_STEP_SHAPE[name], *((m, w) for n, m, w in CEILING_SHAPE if n == name)]
        held.append((SHARD_MATRIX, ""))
    return tuple(marker for marker, _ in held)


def unmarked_template_lines(name: str, text: str) -> list[tuple[int, str]]:
    named = (*workflow_markers(name), *(held for held, _ in EXEMPT_STEPS))
    lines = text.splitlines()
    unmarked = []
    for number, line in enumerate(lines):
        body = line.lstrip(" -")
        indent = len(line) - len(body)
        inside = [at for at in nested(lines, number, indent) if not is_comment(lines[at])]
        if body == "permissions:":
            chunks = [(at, lines[at]) for at in inside if lines[at].strip()]
        elif body.startswith("run:"):
            chunks = [(number, "\n".join([line, *(lines[at] for at in inside)]))]
        else:
            continue
        unmarked += [(at + 1, held.strip()) for at, held in chunks if not mentions(held, named)]
    return unmarked


def mentions(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def nested(lines: list[str], start: int, indent: int) -> list[int]:
    found = []
    for at in range(start + 1, len(lines)):
        rest = lines[at].lstrip(" ")
        if rest and len(lines[at]) - len(rest) <= indent:
            break
        found.append(at)
    return found


def is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def check_merge_workflow(root: Path) -> Iterable[Finding]:
    unmet = "nothing merges a change the judge passes"
    return forge_workflow(root, MERGE_WORKFLOW, unmet=unmet, shape=MERGE_SHAPE)


def check_weekly_workflow(root: Path) -> Iterable[Finding]:
    unmet = "nothing runs the weekly gates, so a finding is seen only when someone thinks to look"
    return forge_workflow(root, WEEKLY_WORKFLOW, unmet=unmet, shape=WEEKLY_SHAPE)


def check_plugin_pin(root: Path) -> Iterable[Finding]:
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
    elif pin.tree is None:
        line = plugin_pin.line_for(plugin_pin.PLUGIN)
        problem = (
            f"the pin names no tree id, so no copy of the plugin can be held to the release; write"
            f" `- Ratchet plugin: {pin.version} at {pin.commit} tree <the tree of that commit>`"
            " (`git rev-parse <commit>^{tree}` in a clone of the plugin gives it), or run the"
            " plugin's upgrade"
        )
        if plugin_pin.commit_of(plugin_pin.PLUGIN) == pin.commit:
            yield Finding(
                BINDING_PATH,
                problem,
                Edit("add the tree id of the plugin in use", plugin_pin.with_pin(line), Agent()),
            )
        else:
            yield Finding(BINDING_PATH, problem, Agent())
    yield from check_pin_recipe(root)
    yield from check_plugin_fallback(root)
    yield from check_plugin_interpreter(root)
    yield from check_jobs_line(root)
    yield from check_pre_commit_recipe(root)
    yield from check_plugin_uses(root)
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
    for path in sorted((root / WORKFLOWS).glob("*.yml")):
        template = read(ADOPT_TEMPLATES / ".github/workflows" / path.name)
        if template is None:
            continue
        unbound = set(after_marker(template.splitlines()).values())
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, following in after_marker(lines).items():
            line = lines[number - 1]
            marker = SUITE_SETUP_MARKER if SUITE_SETUP_MARKER in line else TOOLCHAIN_MARKER
            if not is_comment(line):
                unset = (
                    f"in job `{job_of(lines, number)}` still says '{marker}',"
                    " so no step sets up the project's toolchain"
                )
            elif following.replace(OLD_RUN, NEW_RUN) in unbound or is_marker(following):
                unset = (
                    f"in job `{job_of(lines, number)}` still says '# {marker}' and the"
                    " next line is the template's, so no step sets up the project's toolchain"
                )
            else:
                continue
            yield Finding(
                str(path.relative_to(root)),
                f"line {number} {unset}; add the steps that install it, pinned by commit SHA",
                Agent(),
                identity=unset,
                stops=WEEKLY_CHECKS if path.name == WEEKLY_WORKFLOW else "",
            )


def job_of(lines: list[str], number: int) -> str:
    for line in reversed(lines[: number - 1]):
        if line.startswith("jobs:"):
            break
        if match := WORKFLOW_JOB.match(line):
            return match.group(1)
    return "?"


def is_marker(line: str) -> bool:
    return TOOLCHAIN_MARKER in line or SUITE_SETUP_MARKER in line


def after_marker(lines: list[str]) -> dict[int, str]:
    return {
        number: lines[number] if number < len(lines) else ""
        for number, line in enumerate(lines, start=1)
        if is_marker(line)
    }


def tier_recipes(binding: str, justfile: str) -> dict[str, str]:
    commands = entry_points(binding)
    rows = template_rows()
    named = {}
    for tier in justfile_text.first_dependents(template_file("justfile"), PIN_RECIPE):
        default = rows[tier][1] if tier in rows else f"just {tier}"
        recipe = RUNS_JUST.match((commands.get(tier) or default).strip("`"))
        if recipe is not None and justfile_text.defines_recipe(justfile, recipe.group(1)):
            named[tier] = recipe.group(1)
    return named


def check_plugin_fallback(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    held = PLUGIN_LINE.search(justfile or "")
    template = template_file("justfile")
    line = PLUGIN_LINE.search(template)
    if (
        held is None
        or line is None
        or held.group(0) == line.group(0)
        or justfile_text.template_recipe(template, PLUGIN_GUARD) is None
    ):
        return
    guarded = [
        name
        for name in justfile_text.first_dependents(template, PLUGIN_GUARD)
        if justfile_text.defines_recipe(justfile or "", name)
    ]

    def want(current: str | None) -> str:
        return PLUGIN_LINE.sub(lambda _: line.group(0), current or "", count=1)

    does = f"rewrite the `{PLUGIN_VARIABLE}` line to the template's, which reads the recorded one"
    edit = Edit(
        does,
        gatelib.chained(
            want,
            justfile_text.with_recipe(template, PLUGIN_GUARD),
            *(justfile_text.with_first_dependency(name, PLUGIN_GUARD) for name in guarded),
            *(
                justfile_text.with_template_recipe(template, name)
                for name in justfile_text.callers(template, PLUGIN_GUARD)
                if justfile_text.defines_recipe(justfile or "", name)
            ),
        ),
        Agent(),
    )
    if MARKETPLACE_FALLBACK in held.group(0):
        problem = (
            f"`{PLUGIN_VARIABLE}` falls back to the marketplace clone, which follows the default"
            " branch's head and not a release, so every local tier runs unreleased gates"
        )
    else:
        problem = (
            f"the plugin block above `{PLUGIN_VARIABLE}` is not the template's, so no local run"
            " is held to the release the binding pins"
        )
    yield Finding(
        "justfile", problem, edit, identity="ratchet_plugin falls back to the marketplace"
    )


def bare_template_lines(template: str) -> dict[str, str]:
    bare = {}
    for line in template.split("\n"):
        held = line.replace(f"{{{{{INTERPRETER_VARIABLE}}}}} -P", "python3")
        held = held.replace(f"' + {INTERPRETER_VARIABLE} + '", "python3")
        if held != line:
            bare[held] = line
    return bare


def interpreter_calls(justfile: str, guard_diff: str, template: str) -> str:
    inline = bare_template_lines(template)

    def fixed(line: str) -> str:
        if line in inline:
            return inline[line]
        if line.lstrip().startswith("#"):
            return line
        call = BARE_SCRIPT_CALL.sub(f"{{{{{INTERPRETER_VARIABLE}}}}}", line)
        string = BARE_STRING_CALL.sub(f"{INTERPRETER_VARIABLE} + ' \"' + ratchet_plugin", call)
        return BARE_GUARD_DIFF.sub(lambda _: guard_diff, string)

    return "\n".join(fixed(line) for line in justfile.split("\n"))


RECIPE_HEAD = re.compile(r"^([A-Za-z_][\w-]*)(?:(?P<variable>[ \t]*:=)|[^\n:=]*:(?!=))")


def bare_calls(justfile: str, guard_diff: str, template: str) -> list[str]:
    kind, name = "recipe", ""
    found = []
    for number, line in enumerate(justfile.split("\n"), start=1):
        head = RECIPE_HEAD.match(line)
        if head is not None:
            kind, name = ("variable" if head.group("variable") else "recipe"), head.group(1)
        if interpreter_calls(line, guard_diff, template) != line:
            found.append(
                f"justfile:{number}: {kind} `{name}` runs a plugin script or the template's inline"
                " Python under the bare `python3`"
            )
    return found


def check_plugin_interpreter(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    template = template_file("justfile")
    call = GUARD_DIFF_CALL.search(template)
    block = PLUGIN_LINE.search(template)
    if justfile is None or call is None or block is None or PLUGIN_LINE.search(justfile) is None:
        return
    guard_diff = call.group(0)
    if interpreter_calls(justfile, guard_diff, template) == justfile:
        return

    def want(current: str | None) -> str:
        held = PLUGIN_LINE.sub(lambda _: block.group(0), current or "", count=1)
        return interpreter_calls(held, guard_diff, template)

    calls = "; ".join(bare_calls(justfile, guard_diff, template))
    yield Finding(
        "justfile",
        f"{calls}; the bare `python3` reads PYTHONPATH, PYTHONSTARTUP and the user's site folder,"
        " so a folder on PYTHONPATH can replace a module the gate imports; run each as"
        f" `{{{{{INTERPRETER_VARIABLE}}}}}`",
        Edit(
            f"run each plugin script as `{{{{{INTERPRETER_VARIABLE}}}}}` as the template does",
            want,
            Agent(),
        ),
        identity="a recipe runs a plugin script under the bare python3",
    )


JOBS_LINE = re.compile(r"^jobs[ \t]*:=.*$", re.MULTILINE)
RELEASED_JOBS_LINES = frozenset({'jobs := env_var_or_default("RATCHET_JOBS", num_cpus())'})


def with_template_jobs(template: str) -> gatelib.Want:
    block = "\n".join(
        justfile_text.uncommented(
            (
                *justfile_text.definition(template, "configured_jobs"),
                *justfile_text.definition(template, "jobs"),
            )
        )
    )

    def want(current: str | None) -> str:
        lines = (current or "").split("\n")
        for index, line in enumerate(lines):
            if line in RELEASED_JOBS_LINES:
                start = index
                while start > 0 and lines[start - 1].startswith("#"):
                    start -= 1
                return "\n".join([*lines[:start], block, *lines[index + 1 :]])
        return current or ""

    return want


def check_jobs_line(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    template = template_file("justfile")
    found = JOBS_LINE.search(justfile or "")
    if justfile is None or found is None:
        return
    template_jobs = justfile_text.definition(template, "jobs")
    if not template_jobs or "ratchet.jobs" in justfile_text.code_of(justfile):
        return
    number = justfile[: found.start()].count("\n") + 1
    problem = (
        f"justfile:{number}: `jobs` does not read `git config ratchet.jobs`, so the owner's cap on"
        " gates at once does nothing and tier 0 runs on every core; use the template's"
        " `configured_jobs` and `jobs` lines"
    )
    if found.group(0) in RELEASED_JOBS_LINES:
        fix: Fix = Edit(
            "make `jobs` the template's lines, which read `git config ratchet.jobs`",
            with_template_jobs(template),
            Agent(start=template_jobs),
        )
    else:
        fix = Agent(start=template_jobs)
    yield Finding("justfile", problem, fix, identity="jobs does not read ratchet.jobs")


def exports_run(justfile: str) -> bool:
    return re.search(rf"^export[ \t]+{RUN_VARIABLE}[ \t]*:=", justfile, re.MULTILINE) is not None


def with_run_id(declaration: str) -> gatelib.Want:
    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        anchor = next(
            (i for i, line in enumerate(lines) if line.startswith("ratchet_plugin :=")), None
        )
        bare = next((i for i, line in enumerate(lines) if RUN_UNEXPORTED.match(line)), None)
        if exports_run(text):
            return text
        if bare is not None:
            lines[bare] = f"export {lines[bare]}"
        elif anchor is not None:
            lines.insert(anchor + 1, declaration)
        return "\n".join(lines)

    return want


def check_run_id(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    declaration = next(
        (
            line
            for line in template_file("justfile").split("\n")
            if line.startswith(f"export {RUN_VARIABLE} :=")
        ),
        "",
    )
    if justfile is None or not declaration or exports_run(justfile):
        return
    problem = (
        f"nothing exports `{RUN_VARIABLE}`, so each reader of the shared suite report starts its"
        " own run and the suite runs once per reader (ADR 0071); add the template's line"
    )
    does = f"export `{RUN_VARIABLE}` as the template does"
    fix = Edit(does, with_run_id(declaration), Agent(start=(declaration,)))
    yield Finding("justfile", problem, fix, identity=f"no {RUN_VARIABLE} export")


def check_pre_commit_recipe(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    template = template_file("justfile")
    name = "pre-commit"
    has_staged = justfile is not None and justfile_text.defines_recipe(justfile, "tier0-staged")
    has_tree = justfile is not None and justfile_text.defines_recipe(justfile, "tier0-tree")
    if (
        justfile is None
        or not justfile_text.defines_recipe(justfile, name)
        or not (has_staged or has_tree)
        or justfile_text.template_recipe(template, name) is None
    ):
        return
    quiet = NO_EXIT_MESSAGE in justfile_text.definition(justfile, name)
    if quiet and justfile_text.runs(justfile, name, PIN_SCRIPT):
        return
    fix: Fix = (
        Edit(
            f"make `{name}` the template's recipe",
            justfile_text.with_template_recipe(template, name),
            Agent(),
        )
        if has_staged
        else Agent(start=template_definition(name))
    )
    yield Finding(
        "justfile",
        f"`{name}` neither runs {PIN_SCRIPT} first nor carries `{NO_EXIT_MESSAGE}`, so the commit"
        " hook can run a plugin no check held to the release",
        fix,
        identity="pre-commit recipe is not the template's",
    )


PLUGIN_USE = re.compile(r"\{\{\s*ratchet_plugin\s*\}\}")
PLUGIN_TEXT_LINE = re.compile(
    r'^ratchet_plugin[ \t]*:=[ \t]*"\$RATCHET_PLUGIN_DIR"[ \t]*$', re.MULTILINE
)
PLUGIN_NAME = re.compile(r"\bratchet_plugin\b")
ASSIGNMENT = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:=(.*)$")


def inside_quotes(prefix: str) -> bool:
    outer: list[tuple[bool, bool]] = []
    double = single = False
    index = 0
    while index < len(prefix):
        char = prefix[index]
        if char == '"' and not single:
            double = not double
        elif char == "'" and not double:
            single = not single
        elif prefix.startswith("$(", index) and not single:
            outer.append((double, single))
            double = single = False
            index += 1
        elif char == ")" and outer and not double and not single:
            double, single = outer.pop()
        index += 1
    return double


def body_reads_value(line: str, *, shebang: bool) -> bool:
    return any(
        shebang or not inside_quotes(line[: use.start()]) for use in PLUGIN_USE.finditer(line)
    )


def expression_reads_value(expression: str) -> bool:
    if re.search(r"\([^)]*\bratchet_plugin\b", expression):
        return True
    return any(
        not expression[: use.start()].rstrip().endswith(("\"' +", '" +'))
        for use in PLUGIN_NAME.finditer(expression)
    )


def plugin_value_uses(justfile: str) -> list[str]:
    found: list[str] = []
    shebang = False
    for number, line in enumerate(justfile.split("\n"), 1):
        body = line[:1] in (" ", "\t")
        shebang = (shebang and body) or (body and line.strip().startswith("#!"))
        assignment = None if body else ASSIGNMENT.match(line)
        if body:
            reads = body_reads_value(line, shebang=shebang)
        elif assignment is not None and assignment.group(1) != PLUGIN_VARIABLE:
            reads = expression_reads_value(assignment.group(2))
        else:
            reads = False
        if reads and (shebang or not line.lstrip().startswith("#")):
            found.append(f"line {number}")
    return found


def check_plugin_uses(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    if justfile is None or PLUGIN_TEXT_LINE.search(justfile) is None:
        return
    uses = plugin_value_uses(justfile)
    if uses:
        yield agent(
            f"justfile: {', '.join(uses)} read `{PLUGIN_VARIABLE}` as a folder, and it is the text"
            ' "$RATCHET_PLUGIN_DIR"; write each use as "$RATCHET_PLUGIN_DIR" inside a'
            " double-quoted word of a shell line, and in a shebang recipe read the environment"
            " variable RATCHET_PLUGIN_DIR"
        )


def check_pin_recipe(root: Path) -> Iterable[Finding]:
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
    block = installs.SECTION + markdown_text.section(template_binding(), installs.SECTION).rstrip()
    lines = block.splitlines()
    at = next(i for i, line in enumerate(lines) if INSTALL_BULLET.match(line))
    kept = [line for line in lines if not INSTALL_BULLET.match(line)]
    written = [f"- {label}: {value}" for label, value in bullets.items()]
    return "\n".join([*kept[:at], *written, *kept[at:]])


def missing_install_section(root: Path, text: str, justfile: str | None) -> Finding:
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
    return ", ".join(f"`{name}`" for name in names)


def answered_gates(binding: str) -> set[str]:
    return {
        row.gate
        for row in project_bite.gate_rows(binding)
        if row.tool and not application_types.NONE.match(row.tool) and not later(row.tool)
    }


def check_tier_lists(root: Path, justfile: str) -> Iterable[Finding]:
    tiers = tier_recipes(read(root / BINDING_PATH) or "", justfile)
    for workflow, lists in TIER_LISTS.items():
        if not (root / WORKFLOWS / workflow).is_file():
            continue
        for name, meaning in lists:
            if justfile_text.defines_variable(justfile, name):
                continue
            how = (
                f"with `just --evaluate {name}`"
                if workflow == "deep.yml"
                else "through `ci_jobs.py`"
            )
            problem = (
                f"has no `{name}` variable, which the template's `shape` job of `{workflow}`"
                f" reads {how}; add the template's, naming {meaning}"
            )
            fix = Agent(start=template_definition(name))
            tier = name.split("_")[0]
            if name == ci_jobs.JOBS:
                does = f"add `{name}`: the proof and each gate with a reason alone, then `rest`"
                edit = Edit(does, justfile_text.with_variable(name, default_jobs(root)), fix)
                yield Finding("justfile", problem, edit if readable(justfile) else fix)
            elif name.endswith("_sharded"):
                does = f"add `{name}`, naming no gate to shard"
                edit = Edit(does, justfile_text.with_variable(name, lambda _: '""'), fix)
                yield Finding("justfile", problem, edit)
            elif (recipe := tiers.get(tier)) and justfile_text.dependencies(justfile, recipe):
                does = f"add `{name}`, listing the gates the `{recipe}` recipe runs"
                edit = Edit(does, justfile_text.with_variable(name, gates_of(recipe)), fix)
                yield Finding("justfile", problem, edit)
            else:
                yield Finding("justfile", problem, fix)

    found = ci_jobs.lists(justfile)
    ci = read(root / WORKFLOWS / "ci.yml")
    if found is not None and ci is not None:
        yield from job_list_findings(found, ci_jobs.facts(root), justfile=justfile, workflow=ci)


def readable(justfile: str) -> bool:
    return all(
        justfile_text.ordered(justfile, name) is not None
        or not justfile_text.defines_variable(justfile, name)
        for name in (ci_jobs.GATES, ci_jobs.SHARDED)
    )


def default_jobs(root: Path) -> Callable[[str], str]:
    known = ci_jobs.facts(root)

    def value(justfile: str) -> str:
        gates = justfile_text.ordered(justfile, ci_jobs.GATES) or ()
        sharded = frozenset(justfile_text.ordered(justfile, ci_jobs.SHARDED) or ())
        return '"' + " ".join(ci_jobs.default(gates, sharded, known)) + '"'

    return value


JOB_FIXES: dict[ci_jobs.Kind, Callable[[str], tuple[str, Callable[[list[str]], list[str]]]]] = {
    ci_jobs.Kind.DROPPED: lambda _: (
        f"add `{ci_jobs.REST}` to `{ci_jobs.JOBS}`",
        ci_jobs.with_rest,
    ),
    ci_jobs.Kind.GROUPED: lambda gate: (f"give `{gate}` a job of its own", ci_jobs.given_own(gate)),
    ci_jobs.Kind.DRIFT: lambda gate: (
        f"move `{gate}` into `{ci_jobs.REST}`",
        ci_jobs.into_rest(gate),
    ),
}


OWN_JOB_LIST = "the project's own job list"
OFFERED = frozenset({OWN_JOB_LIST})


def job_list_findings(
    found: ci_jobs.Lists | ci_jobs.Own, known: ci_jobs.Facts, *, justfile: str, workflow: str
) -> Iterable[Finding]:
    if isinstance(found, ci_jobs.Own) and ci_jobs.SCRIPT in workflow:
        refused = (
            f"{found.reason}, and `{ci_jobs.SCRIPT}` in `{WORKFLOWS / 'ci.yml'}` reads it, so the"
            f" shape job refuses it; write `{ci_jobs.JOBS}` as one plain string of gates of"
            f" `{ci_jobs.GATES}`, `{ci_jobs.REST}` and `NAME=gate,gate` groups"
        )
        yield Finding("justfile", refused, Agent())
        return
    if isinstance(found, ci_jobs.Own):
        left = f"{found.reason}, so the plugin left {OWN_JOB_LIST} as it is"
        start = (f"the template's form of `{ci_jobs.JOBS}` and its CI:", *regroup_difference())
        yield Finding("justfile", left, Agent(start=start), identity=OWN_JOB_LIST)
        return
    read_in_ci = ci_jobs.SCRIPT in workflow or ci_jobs.JOBS in workflow
    writes = not read_in_ci or ci_jobs.routed(workflow, justfile)
    for problem in ci_jobs.problems(found, known):
        made = JOB_FIXES.get(problem.kind)
        if made is None or not writes:
            yield Finding("justfile", problem.text, Agent())
            continue
        does, change = made(problem.gate)
        edit = Edit(does, justfile_text.with_words(ci_jobs.JOBS, change), Agent())
        yield Finding("justfile", problem.text, edit)


def gates_of(recipe: str) -> Callable[[str], str]:
    def value(justfile: str) -> str:
        listed = justfile_text.dependencies(justfile, recipe) or ()
        return '"' + " ".join(name for name in listed if name != PIN_RECIPE) + '"'

    return value


def runs_from_plugin(row: project_bite.Row) -> bool:
    return bool(project_bite.SHIPPED.fullmatch(row.tool)) or (
        row.gate in CARRIED_GATES and row.bound
    )


def tier_list_of(justfile: str, recipe: str) -> str | None:
    name = recipe.replace("-", "_") + "_gates"
    lines = justfile.split("\n")
    block = next((b for b in justfile_text.blocks(lines) if b.name == recipe and b.parsed), None)
    body = justfile_text.code_of("\n".join(lines[block.head + 1 : block.end])) if block else ""
    if name not in justfile_text.INTERPOLATION.findall(body):
        return None
    values = justfile_text.variables(justfile)
    built = justfile_text.BARE_NAME.findall(
        justfile_text.STRING.sub(" ", values.get(name, "")) if name in values else ""
    )
    return next(
        (
            one
            for one in (name, *built)
            if justfile_text.QUOTED.fullmatch(values.get(one, "").strip())
        ),
        None,
    )


def unrun_gates(binding: str, justfile: str) -> Iterable[Finding]:
    tiers = tier_recipes(binding, justfile)
    template = template_file("justfile")
    for row in project_bite.gate_rows(binding):
        tier = f"tier{row.tier}"
        if not runs_from_plugin(row) or tier not in ("tier0", "tier1"):
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
        listing = tier_list_of(justfile, target)
        bodiless = justfile_text.dependencies(justfile, target) is not None
        if reasons or not (bodiless or listing):
            yield Finding("justfile", problem, fix, stops=row.question)
            continue
        copied = [] if defined else [justfile_text.with_recipe(template, row.gate)]
        if bodiless:
            wants = [*copied, justfile_text.with_dependency(target, row.gate)]
        else:
            wants = [*copied, justfile_text.with_listed(listing or "", row.gate)]
        if target == recipe and bodiless:
            wants.append(justfile_text.with_listed(f"{tier}_gates", row.gate))
        want = gatelib.chained(*wants)
        does = f"run `{row.gate}` in `{target}`" + (
            "" if defined else ", from the template's recipe"
        )
        yield Finding("justfile", problem, Edit(does, want, fix), stops=row.question)


@dataclass(frozen=True)
class Matrix:
    start: int
    end: int
    gates: tuple[str, ...]


def static_matrix(lines: list[str], job: str) -> Matrix | None:
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
    path = WORKFLOWS / "ci.yml"
    lines = (read(root / path) or "").split("\n")
    matrix = static_matrix(lines, "tier1")
    if matrix is None:
        return
    for row in project_bite.gate_rows(binding):
        if row.tier != "1" or not runs_from_plugin(row):
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
    return "hooks-installed" in ratchet or "hooks_installed.py" in ratchet


def ratchet_asks(justfile: str) -> Finding:
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


def with_loop_budget(recipe: str) -> gatelib.Want:
    template = template_file("justfile")
    block = next((b for b in justfile_text.blocks(template.split("\n")) if b.name == recipe), None)
    after = "\n".join(template.split("\n")[block.head : block.end]) if block is not None else ""
    before = after.replace(f" {LOOP_BUDGET_FLAG}", "", 1)

    def want(current: str | None) -> str:
        text = current or ""
        if not before or before == after:
            return text
        lines = text.split("\n")
        found = next((b for b in justfile_text.blocks(lines) if b.name == recipe), None)
        if found is None:
            return text
        held = "\n".join(lines[found.head : found.end])
        return text.replace(before, after, 1) if before in held else text

    return want


def check_loop_budget(justfile: str) -> Iterable[Finding]:
    for recipe in LOOP_RECIPES:
        body = "\n".join(justfile_text.definition(justfile, recipe))
        code = justfile_text.code_of(body)
        if ("{{parallel}}" not in code and "parallel.py" not in code) or (
            LOOP_BUDGET_FLAG in code or "--budget=loop" in code
        ):
            continue
        problem = (
            f"the `{recipe}` recipe runs `parallel.py` without `{LOOP_BUDGET_FLAG}`,"
            " so local checks never judge the loop budget"
        )
        does = f"pass `{LOOP_BUDGET_FLAG}` to `parallel.py` in `{recipe}`"
        edit = Edit(does, with_loop_budget(recipe), Agent(start=template_definition(recipe)))
        yield Finding("justfile", problem, edit, identity=f"no {LOOP_BUDGET_FLAG} on {recipe}")


def check_runner(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    if justfile is None:
        return
    yield from unrun_gates(read(root / BINDING_PATH) or "", justfile)
    yield from ci_unrun(root, read(root / BINDING_PATH) or "")
    yield from check_tier_lists(root, justfile)
    yield from check_loop_budget(justfile)
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
    if path.is_dir():
        files = [item for item in sorted(path.iterdir()) if item.is_file()]
        return "\n".join(f"{item.name}\n{read(item) or ''}" for item in files)
    return read(path)


def check_framework_hooks(text: str) -> Iterable[Finding]:
    if not (INSTALLS_COMMIT_MSG.search(text) and COMMIT_MSG_STAGE.search(text)):
        yield agent(
            f"{COMMIT_HOOKS}: no commit-msg hook installed, so a bad subject is first seen in CI"
        )
    if WHOLE_TIER0_ENTRY.search(text):
        yield agent(f"{COMMIT_HOOKS}: {WHOLE_TIER0}")


def check_other_hooks(name: str, text: str, install: str | None) -> Iterable[Finding]:
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
        yield agent(
            f"justfile: the `pre-commit` and `commit-msg` recipes do not answer "
            f"{hooks_installed.PROBE_ENV}, so `hooks_installed.py` cannot tell whether a commit "
            "would run tier 0; copy them from the template justfile",
            start=template_definition("pre-commit") + template_definition("commit-msg"),
        )


def settings_finding(
    found: settings_rules.Drift, manifest: dict[str, object], text: str | None
) -> Finding:
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
    ceilings = data.get("ceilings")
    table = ceilings.get("change") if isinstance(ceilings, dict) else None
    return isinstance(table, dict) and any(key in table for key in ("files", "lines"))


def moved_change_ceiling(text: str | None) -> str:
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


def check_bite_all_ceiling(root: Path) -> Iterable[Finding]:
    text = read(root / "quality/thresholds.toml")
    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return
    ceilings = data.get("ceilings")
    seconds = ceilings.get("seconds") if isinstance(ceilings, dict) else None
    if isinstance(seconds, dict) and budget.ceiling_of(text or "", budget.FULL_PROOF) is None:
        yield Finding(
            "quality/thresholds.toml",
            full_proof_problem(root, text or ""),
            Agent(
                then=(
                    (
                        "run `just budget bite-all --set`, which records the ceiling once at least"
                        " five green runs are in `.ratchet/timing.jsonl` (from `just check-deep`"
                        " or deep CI runs)"
                    ),
                )
            ),
        )


def check_cache_written(root: Path) -> Iterable[Finding]:
    for path in runrecord.facts(root / runrecord.TIMING).missing or []:
        yield agent(
            f"{BINDING_PATH}: the Local caches bullet names {path}, which did not"
            " exist after the last tier run",
            then=(
                (
                    "make the recipe that owns the cache write it, or take the path off the"
                    " Local caches bullet, then run the tier again"
                ),
            ),
        )


def check_cache_restored(root: Path) -> Iterable[Finding]:
    binding = read(root / BINDING_PATH) or ""
    named = runrecord.ci_cache_paths(binding)
    if not named:
        return
    restored = runrecord.facts(root / runrecord.TIMING).restored
    for paths, size in restored.items():
        if size == 0:
            what = ", ".join(paths) if paths else ", ".join(named)
            yield agent(
                f"{BINDING_PATH}: the Cache line names {what}, and the last CI cache restore"
                " restored 0 bytes",
                then=(
                    (
                        "check the workflow's cache path and restore-keys, or take the path off"
                        " the Cache line if CI does not cache it"
                    ),
                ),
            )


def check_duplicate_producers(root: Path) -> Iterable[Finding]:
    binding = read(root / BINDING_PATH)
    if binding is None:
        return
    producers = report.producers(binding)
    if not producers:
        return
    facts = runrecord.facts(root / runrecord.TIMING)
    for report_name, producer_recipe in producers:
        latest = facts.latest_producer_run.get(producer_recipe)
        if latest is None:
            continue
        count = facts.producer_counts.get((latest, producer_recipe), 0)
        if count > 1:
            yield agent(
                f"{BINDING_PATH}: the shared report '{report_name}' producer"
                f" '{producer_recipe}' ran {count} times in run '{latest}'",
                then=(
                    (
                        "ensure every reader reads the report through scripts/report.py and nothing"
                        " mutates tracked files mid-run, then run the tier again"
                    ),
                ),
            )


def full_proof_problem(root: Path, text: str) -> str:
    tier = budget.ceiling_of(text, "tier2")
    start = "`[ceilings.seconds]` has no `bite-all`, so the full proof after a merge "
    if tier is None:
        return start + "has no ceiling at all, since no `tier2` ceiling is recorded either"
    problem = f"{start}is held to tier 2's ceiling of {tier:g}s and goes red when it runs longer"
    last = last_full_proof(root)
    return problem + (f"; the last full proof took {last:g}s" if last is not None else "")


def last_full_proof(root: Path) -> float | None:
    return runrecord.facts(root / runrecord.TIMING).seconds.get(budget.FULL_PROOF)


def check_gate_reads(root: Path) -> Iterable[Finding]:
    binding = read(root / BINDING_PATH) or ""
    gates = {row.gate for row in project_bite.gate_rows(binding)}
    heading = project_bite.READS
    tracked = gatelib.tracked_paths(root)
    reads, strays = project_bite.read_reads(binding)
    for stray in strays:
        yield agent(f"{BINDING_PATH}: '{heading}' has a line that is no bullet: '{stray}'")
    for gate, entries in reads.items():
        if gate not in gates:
            yield agent(f"{BINDING_PATH}: '{heading}' has a bullet for '{gate}', no entry point")
        if not entries:
            yield agent(f"{BINDING_PATH}: '{heading}' has an empty bullet for '{gate}'")
        for entry in entries:
            if not any(project_bite.reading(path, entry) for path in tracked):
                yield agent(f"{BINDING_PATH}: gate '{gate}' reads '{entry}', matching no file")


def check_bites(root: Path) -> Iterable[Finding]:
    return bite_findings(project_bite.registry_check(root))


def bite_recipe_sharding(justfile: str) -> Iterable[Finding]:
    template = template_file("justfile")
    for variable, recipe in SHARDED_BITE_RECIPES:
        sharded = justfile_text.ordered(justfile, variable) or ()
        defn = justfile_text.definition(justfile, recipe)
        if recipe not in sharded or not defn:
            continue
        if SHARD_FLAG.search(justfile_text.code_of("\n".join(defn))):
            continue
        problem = (
            f"`{recipe}` is in `{variable}`, but its recipe does not pass"
            ' `--shard "${SHARD:-1}/${SHARDS:-1}"` (so each shard would run every fault)'
        )
        reasons = justfile_text.unmet(template, justfile, recipe)
        agent_fix = Agent(start=template_definition(recipe), then=reasons)
        if not reasons and "\n".join(justfile_text.uncommented(defn)) == OLD_BITE_RECIPES[recipe]:
            does = (
                f'pass `--shard "${{SHARD:-1}}/${{SHARDS:-1}}"` in `{recipe}`, as the template does'
            )
            fix: Fix = Edit(does, justfile_text.with_template_recipe(template, recipe), agent_fix)
        else:
            fix = agent_fix
        yield Finding("justfile", problem, fix)


def bite_shard_check(root: Path) -> Iterable[Finding]:
    binding = read(root / BINDING_PATH) or ""
    shards = ci_jobs.shard_count(binding)
    source = read(root / project_bite.REGISTRY)
    if shards is None or source is None:
        return
    registry, problems = project_bite.read_registry(source)
    if problems or not registry.bites:
        return
    rows = project_bite.gate_rows(binding)
    selected = [(row, [bite for bite in registry.bites if bite.gate == row.gate]) for row in rows]
    tasks = project_bite.fault_tasks(selected, ())
    if not tasks:
        return
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = project_bite.check_shards(tasks, shards)
    if code != 0:
        yield agent(
            f"{project_bite.REGISTRY}: shard check for {shards} shards failed:"
            f" {out.getvalue().strip()}"
        )


def check_bite_sharding(root: Path) -> Iterable[Finding]:
    justfile = read_justfile(root)
    if justfile is not None:
        yield from bite_recipe_sharding(justfile)
    yield from bite_shard_check(root)


def bite_findings(bites: project_bite.RegistryCheck) -> Iterator[Finding]:
    path = str(project_bite.REGISTRY)
    for problem in bites.failures:
        if problem == project_bite.MISSING:
            yield whole_file(path, project_bite.MISSING)
        else:
            identity = project_bite.owed_key(problem)[2]
            yield Finding(path, str(problem), Agent(), identity=identity)


def check_incidents(root: Path) -> Iterable[Finding]:
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


CHECKS: tuple[Check, ...] = (
    check_plugin_pin,
    check_agent_instructions,
    check_companion_lines,
    check_glossary,
    check_brief,
    check_adrs,
    check_binding,
    check_architecture,
    check_triggers,
    check_trigger_kinds,
    check_formal_verification,
    check_dependencies,
    check_suppressions,
    check_comments_section,
    check_documents,
    check_signs,
    check_pins,
    check_mutation,
    check_mutation_filter,
    check_operability,
    check_speed,
    check_incremental,
    check_shared_reports,
    check_changed_tests,
    check_manifest,
    check_default_branch,
    check_doors,
    check_run_id,
    check_merge_workflow,
    check_weekly_workflow,
    check_toolchain,
    check_settings,
    check_commit_hooks,
    check_runner,
    check_installs,
    check_quality,
    check_bite_all_ceiling,
    check_cache_written,
    check_cache_restored,
    check_duplicate_producers,
    check_local_caches,
    check_gate_reads,
    check_bites,
    check_bite_sharding,
    check_incidents,
    check_disputes,
    check_features,
    check_pr_template,
)


def rule_of(check: Check) -> str:
    return check.__name__.removeprefix("check_")


def raised(
    root: Path, bites: project_bite.RegistryCheck | None = None
) -> list[tuple[Check, Finding]]:
    told = project_bite.registry_check(root) if bites is None else bites
    found = [
        (check, replace(failure, rule=rule_of(check)))
        for check in CHECKS
        for failure in (bite_findings(told) if check is check_bites else check(root))
    ]
    if any(isinstance(failure.fix, Auto) for _, failure in found):
        message = "a check offers an Edit: only the trial makes a finding auto"
        raise TypeError(message)
    return found + ledger_raised(root, [failure.key() for _, failure in found])


def ledger_raised(root: Path, keys: list[owed.Key]) -> list[tuple[Check, Finding]]:
    upgrade_line = f"run `{plugin_pin.upgrade_command(plugin_pin.PLUGIN)}`, which drops them"

    def check_owed(tree: Path) -> Iterator[Finding]:
        ledger = owed.read(tree)
        for problem in ledger.problems:
            yield Finding(str(owed.LEDGER), problem, Agent(), rule=owed.OWN)
        mine = [entry for entry in ledger.entries if entry.rule not in owed.GATE_RULES]
        _, stale = owed.excused(keys, mine)
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
            Edit(does, owed.kept(Counter(keys)), Agent(then=(upgrade_line,))),
            rule=owed.OWN,
        )

    return [(check_owed, failure) for failure in check_owed(root)]


def findings(
    root: Path, *, owned: bool = False, bites: project_bite.RegistryCheck | None = None
) -> list[Finding]:
    found = raised(root, bites)
    if not any(isinstance(failure.fix, Edit) for _, failure in found):
        return [failure for _, failure in found]
    with contextlib.ExitStack() as stack:
        tree = root
        if not owned:
            tree = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="ratchet-trial-")))
            copied(root, tree)
        raised_after: Raised = {}
        listed = gatelib.listings.set({})
        try:
            return [
                tried(tree, check, failure, raised_after=raised_after) for check, failure in found
            ]
        finally:
            gatelib.listings.reset(listed)


def failures(root: Path) -> list[str]:
    return [str(finding) for _, finding in raised(root)]


def owed_split(root: Path, found: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    excused, _ = owed.excused([finding.key() for finding in found], owed.read(root).entries)
    held = [hit or finding.identity in OFFERED for finding, hit in zip(found, excused, strict=True)]
    red = [finding for finding, hit in zip(found, held, strict=True) if not hit]
    return red, [finding for finding, hit in zip(found, held, strict=True) if hit]


def not_running(root: Path, found: Sequence[Finding]) -> list[str]:
    binding = read(root / BINDING_PATH) or ""
    kinds, _ = triggers.parse(binding)
    stuck = [
        row.question
        for row in project_bite.gate_rows(binding)
        if (waits := application_types.LATER.match(row.tool))
        and set(application_types.code_kinds(waits.group(1).strip())) - set(kinds)
    ]
    return list(dict.fromkeys([*(finding.stops for finding in found if finding.stops), *stuck]))


def then_lines(fix: Fix) -> tuple[str, ...]:
    judged = fix.otherwise if isinstance(fix, Edit) else fix
    return judged.then if isinstance(judged, Agent | Auto) else ()


def run(root: Path) -> int:
    bites = project_bite.registry_check(root)
    with runrecord.read_once():
        found = findings(root, bites=bites)
        slows = project_bite.recorded_slow(root)
    red, held = owed_split(root, found)
    owing = [finding for finding in held if finding.identity not in OFFERED]
    for finding in red:
        print(finding)
        for line in then_lines(finding.fix):
            print(f"  then: {line}")
    for finding in owing:
        print(f"owed: {finding}")
    for finding in held:
        if finding.identity in OFFERED:
            print(f"offered: {finding}")
    for unchecked in bites.unchecked:
        print(f"not checked: {project_bite.REGISTRY}: {unchecked}")
    for slow in slows:
        print(f"slow: {slow}")
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
        *report_mutations(),
        *changed_tests_mutations(),
        *mutation_gate_mutations(),
        *dependency_mutations(),
        *weekly_mutations(),
        *record_mutations(),
        *trigger_mutations(),
        *upgrade_mutations(),
        *owed_mutations(),
        *architecture_mutations(),
        *comments_mutations(),
        *documents_mutations(),
    ]


def documents_mutations() -> list[Mutation]:
    return [
        ("binding without a Documents section", rebind(documents.SECTION, "## Pages")),
        (
            "Documents row left as the template wrote it",
            rebind(SELF_TEST_DOCUMENTS, "| <kind> | <`globs` of its files> | <`never`> |"),
        ),
        (
            "Documents row that is neither never nor pass",
            rebind(SELF_TEST_DOCUMENTS, "| self-test pages | `**/*.md` | sometimes |"),
        ),
    ]


def comments_mutations() -> list[Mutation]:
    published = f"- {comments.PUBLISHED}: {comments.NOT_A_LIBRARY}"
    return [
        ("binding without a Comments section", rebind(comments.SECTION, "## Remarks")),
        (
            "Comments row left as the template wrote it",
            rebind(SELF_TEST_COMMENTS, "| <`globs` of one kind of file> | <`listed`> |"),
        ),
        (
            "Comments row that is neither listed nor none with a reason",
            rebind(SELF_TEST_COMMENTS, "| `**` | sometimes |"),
        ),
        ("binding without a published interface line", rebind(published + "\n", "")),
        (
            "documentation coverage bound outside a library",
            rebind(
                f"| {LIBRARY_ONLY} | {comments.NOT_A_LIBRARY} |", f"| {LIBRARY_ONLY} | a tool |"
            ),
        ),
    ]


def architecture_mutations() -> list[Mutation]:
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
            rewrite(
                "justfile", "ratchet: _plugin\n    just hooks-installed\n", "ratchet: _plugin\n"
            ),
        ),
        (
            "justfile capping the gates at once by another variable",
            rewrite("justfile", '"RATCHET_JOBS"', '"JOBS"'),
        ),
        (
            "justfile timing perf and load beside other gates",
            rewrite(
                "justfile", 'alone := "--alone perf load mutation test-stability"', 'alone := ""'
            ),
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
        (
            "justfile check without --budget loop",
            rewrite("justfile", " --budget loop", ""),
        ),
    ]


def artefact_mutations() -> list[Mutation]:
    ci = str(WORKFLOWS / "ci.yml")
    return [
        ("AGENTS.md without the method stanza", rewrite("AGENTS.md", "ratchet:method", "nothing")),
        (
            "AGENTS.md with a companion line that says more than the plugin's name",
            rewrite(
                "AGENTS.md",
                plain_companion(companions()[0]),
                f"- **A question?** `{companions()[0]}`: a rule",
                count=1,
            ),
        ),
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
    def mutate(root: Path) -> None:
        first(root)
        second(root)

    return mutate


def rebind(old: str, new: str) -> Callable[[Path], None]:
    return rewrite(BINDING_PATH, old, new)


def type_mutations() -> list[Mutation]:
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
    return rewrite(str(project_bite.REGISTRY), old, new, count=1)


def bite_mutations() -> list[Mutation]:
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


TIER1_JOBS_LINE = 'tier1_jobs := "test mutation-changed bite rest"'
BARE_PROOF = 'tier1_jobs := "test mutation-changed bite"'
ALONE_AUDIT = 'tier1_jobs := "test mutation-changed bite audit rest"'
AUDIT_CEILING = (
    "quality/thresholds.toml",
    "[ceilings.change]",
    "[ceilings.seconds]\naudit = 60\n\n[ceilings.change]",
)


def speed_mutations() -> list[Mutation]:
    stated = "stated for this stack"
    ci, deep = str(WORKFLOWS / "ci.yml"), str(WORKFLOWS / "deep.yml")
    return [
        ("CI whose ceilings block a pull request", rewrite(ci, " --over report", "")),
        (
            "CI job without .ratchet/timing.jsonl in its cache path",
            rewrite(ci, f"            {TIMING_LOG}\n", "", count=1),
        ),
        ("nightly that holds no job to its tier's ceiling", rewrite(deep, " --tier tier2", "")),
        ("binding without a Speed section", rebind(SPEED_SECTION, "## Slowness")),
        ("Speed section without a part", rebind("- Cache: ", "- Store: ")),
        (
            "Speed section with a part left unfilled",
            rebind(f"- Parallel: {stated}", "- Parallel: <how>"),
        ),
        ("Speed section with no shard count", rebind("- Shards: 1", "- Shards: a few")),
        ("Speed section without an incremental line", rebind("- Incremental: ", "- Mode: ")),
        (
            "Speed section with an incremental line of none and no reason",
            rebind(f"- Incremental: {stated}", "- Incremental: none:"),
        ),
        ("CI workflow without concurrency control", rewrite(ci, "concurrency:", "serial:")),
        ("CI workflow without a cache", rewrite(deep, "cache", "store")),
        (
            "CI workflow whose cache key has no per-run part",
            rewrite(ci, "-${{ github.sha }}", "", count=1),
        ),
        (
            "CI workflow whose matrix job cache key names no matrix value",
            rewrite(ci, "-${{ matrix.gate }}-", "-", count=1),
        ),
        ("CI workflow without a shard matrix", rewrite(ci, SHARD_MATRIX, "matrix.part")),
        ("CI that reads no job list for tier 1", rewrite(ci, JOB_LIST_SHAPE[0], "jobs.py")),
        ("CI without RATCHET_RUN", rewrite(ci, CI_RUN_MARKER, "RATCHET_OLD: 1")),
        ("CI without the loop job", rewrite(ci, CI_LOOP_MARKER, "echo skip")),
        (
            "CI loop job without actions: read",
            rewrite(ci, "      actions: read\n", ""),
        ),
        ("CI loop job without always()", rewrite(ci, "always() && ", "")),
        ("CI loop job omitting a job from needs", rewrite(ci, ", tier1-long]", "]")),
        (
            "job list that drops the gates no job names",
            rewrite("justfile", TIER1_JOBS_LINE, BARE_PROOF),
        ),
        (
            "job list with a gate alone for no reason",
            rewrite("justfile", TIER1_JOBS_LINE, ALONE_AUDIT),
        ),
        ("group that holds a gate with a ceiling of its own", rewrite(*AUDIT_CEILING)),
        ("nightly without bite slice check", rewrite(deep, DEEP_SHARD_CHECK_MARKER, "--no-check")),
        (
            "nightly whose tier2-long job skips slice shards",
            rewrite(deep, "needs.shape.outputs.shards", "[1]"),
        ),
        (
            "nightly whose tier2-long job omits SHARDS",
            rewrite(deep, "SHARDS: ${{ needs.shape.outputs.count }}", "OTHER: 1"),
        ),
        (
            "sharded bite recipe that omits --shard",
            rewrite(
                "justfile",
                'project_bite.py" --changed --shard "${SHARD:-1}/${SHARDS:-1}"',
                'project_bite.py" --changed',
            ),
        ),
        (
            "sharded bite-all recipe that omits --shard",
            rewrite(
                "justfile",
                'project_bite.py" --shard "${SHARD:-1}/${SHARDS:-1}"',
                'project_bite.py"',
            ),
        ),
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
            "manifest letting an agent write the deep record",
            rewrite(manifest, f'    "{DEEP_RECORD}",\n', ""),
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
            rewrite(binding, f"0.0.0 at {pinned} tree {pinned}", PLUGIN_PIN_PLACEHOLDER),
        ),
        (
            "binding whose plugin pin names no tree id",
            rewrite(binding, f"0.0.0 at {pinned} tree {pinned}", f"0.0.0 at {pinned}"),
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
    (root / "self-test.lock").write_text("locked\n", encoding="utf-8")


def dependency_mutations() -> list[Mutation]:
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


def report_mutations() -> list[Mutation]:
    row = SELF_TEST_REPORTS
    shared = (
        "| suite | `just build` | `suite.xml` | `just coverage`, `just diff-coverage` |"
        " `just coverage` |"
    )
    reading = rewrite("justfile", "coverage: _plugin\n", "coverage: _plugin\n    cat suite.xml\n")

    sharing = rebind(row, shared)

    def reading_directly(root: Path) -> None:
        sharing(root)
        reading(root)

    def duplicate_producer(root: Path) -> None:
        sharing(root)
        path = root / runrecord.TIMING
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"kind": runrecord.PRODUCER, "report": "suite", "producer": "build", "run": "run-1"},
            {"kind": runrecord.PRODUCER, "report": "suite", "producer": "build", "run": "run-1"},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    return [
        ("binding without a Shared reports section", rebind(report.SECTION, "## Reports")),
        (
            "Shared reports row that says none with no reason",
            rebind(row, "| none: x | none | none | none | none |"),
        ),
        (
            "Shared reports row left as the template wrote it",
            rebind(
                row,
                "| `suite` | `just suite-report` | <`path` of the coverage report, Cobertura or"
                " LCOV>, <`path` of the test results, JUnit XML> | `just test`, `just coverage`,"
                " `just diff-coverage`, `just test-counts` | `just test` |",
            ),
        ),
        (
            "Shared reports row whose producer is no recipe of the justfile",
            rebind(row, shared.replace("just build", "just no-such-recipe")),
        ),
        ("reader recipe that opens its shared report's path itself", reading_directly),
        ("shared report producer ran more than once in one run", duplicate_producer),
    ]


def changed_tests_mutations() -> list[Mutation]:
    row = SELF_TEST_SUITES
    unit = "| unit | `just test` | `tests/**` | `just test-files` |"
    header = "| Suite | Gate | Files | Run files |"
    ungated = unit.replace("just test`", "just no-such-gate`")
    unreasoned = unit.replace("`just test-files`", "")
    filesless = rewrite("justfile", "test-files *files:", "test-files:")
    binding = rebind(row, unit)

    def taking_none(root: Path) -> None:
        binding(root)
        filesless(root)

    return [
        ("Tests section without a suites table", rebind(header, "| Name | Gate | Files | Run |")),
        (
            "suites row that says none with no reason",
            rebind(row, "| none: x | none | none | none |"),
        ),
        ("suites row naming a gate the Gates table lacks", rebind(row, ungated)),
        ("suites row with neither a way to run files nor a reason", rebind(row, unreasoned)),
        ("suites row whose recipe takes no files", taking_none),
        (
            "justfile without the tests-changed recipe",
            rewrite("justfile", "tests-changed *suites: _plugin", "tests-moved *suites: _plugin"),
        ),
    ]


def mutation_gate_mutations() -> list[Mutation]:
    def passing_a_report(root: Path) -> None:
        text = (root / "justfile").read_text(encoding="utf-8")
        script = '"{{ratchet_plugin}}/scripts/mutation_survivors.py"'
        recipe = f"\nmutation-own:\n    python3 {script} --tool x --report r.json\n"
        (root / "justfile").write_text(text + recipe, encoding="utf-8")

    def unfiltered(root: Path) -> None:
        text = (root / "justfile").read_text(encoding="utf-8")
        script = '"{{ratchet_plugin}}/scripts/mutation_survivors.py"'
        capture = f'files="$(python3 {script} --tool x --changed)"; [ -z "$files" ] && exit 0'
        recipe = f'\nmutation-own:\n    {capture}; x-tool run --mutate "$files"\n'
        (root / "justfile").write_text(text + recipe, encoding="utf-8")

    return [
        ("mutation recipe that passes a tool's own report", passing_a_report),
        ("mutation recipe that hands --changed output to its tool unfiltered", unfiltered),
    ]


def owed_mutations() -> list[Mutation]:
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


UNBOUND_GROUP = re.compile(r"^paths = \[\]$", re.MULTILINE)
TYPES_PLACEHOLDER = re.compile(r"^- Application types: <[^\n]*>$", re.MULTILINE)
EMPTY_TOOL_CELL = re.compile(r"^(\| [^|\n]+ \|) \|", re.MULTILINE)
TEST_PATHS_PLACEHOLDER = re.compile(r"^- Tests: <[^\n]*>$", re.MULTILINE)
TRIGGERS_PLACEHOLDER = re.compile(r"^\| <kind> \|[^\n]*$", re.MULTILINE)
INCREMENTAL_PLACEHOLDER = re.compile(r'"bind: the mutation tool\'s option[^"]*"')
SPEED_PLACEHOLDER = re.compile(
    r"^(- (?!Local caches|Model:|Mirrors:|Synced:)[A-Z][a-z ]+: )<[^\n]*>$", re.MULTILINE
)
INSTALL_PLACEHOLDER = re.compile(r"^- Install: <[^\n]*>$", re.MULTILINE)
DEPENDENCY_PLACEHOLDER = re.compile(
    r"^- (Manifests|Lockfiles|Entry point): <[^\n]*>$", re.MULTILINE
)
REGISTRIES_PLACEHOLDER = re.compile(r"^- Registries: <[^\n]*>$", re.MULTILINE)
SUPPRESSION_KIND_PLACEHOLDER = re.compile(r"^\| <suppression kind> \|[^\n]*$", re.MULTILINE)
SUPPRESSION_QUESTION_PLACEHOLDER = re.compile(r"^\| <question> \|[^\n]*$", re.MULTILINE)
SELF_TEST_KIND = "self-test-silence"
SIGNS_PLACEHOLDER = re.compile(
    r"^\| (deployed|irreplaceable-data|depended-on) \| <[^\n]*$", re.MULTILINE
)
PINS_PLACEHOLDER = re.compile(r"^\| <`globs` of the files that name an image[^\n]*$", re.MULTILINE)
SELF_TEST_PINS = "| none: the self-test names no image | none |"
COMMENTS_PLACEHOLDER = re.compile(r"^\| <`globs` of one kind of file> \|[^\n]*$", re.MULTILINE)
SELF_TEST_COMMENTS = "| `**` | none: the self-test writes no code by hand |"
DOCUMENTS_PLACEHOLDER = re.compile(r"^\| <kind> \| <`globs` of its files>[^\n]*$", re.MULTILINE)
SELF_TEST_DOCUMENTS = "| self-test pages | `**/*.md` | `never` |"
PUBLISHED_PLACEHOLDER = re.compile(r"^- Published interface: <[^\n]*>$", re.MULTILINE)
REPORTS_PLACEHOLDER = re.compile(r"^\| `suite` \|[^\n]*$", re.MULTILINE)
SELF_TEST_REPORTS = "| none: the self-test shares no report | none | none | none | none |"
SUITES_PLACEHOLDER = re.compile(r"^\| <suite name> \|[^\n]*$", re.MULTILINE)
SELF_TEST_SUITES = "| none: the self-test has no tests | none | none | none |"
LATER_SETUP_PLACEHOLDER = re.compile(rf"^[^\n]*{SUITE_SETUP_MARKER}[^\n]*\n", re.MULTILINE)
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
    text = text.replace(PLUGIN_PIN_PLACEHOLDER, f"0.0.0 at {'0' * 40} tree {'0' * 40}")
    text = TYPES_PLACEHOLDER.sub("- Application types: cli", text)
    text = TEST_PATHS_PLACEHOLDER.sub("- Tests: `tests/**`", text)
    text = DECISION_PLACEHOLDER.sub(f"- Decision: `{SELF_TEST_RECORD}`", text)
    text = PARTS_PLACEHOLDER.sub(SELF_TEST_PARTS, text)
    text = text.replace("| contract drift | |", "| contract drift | none: not a service |")
    text = text.replace(f"| {LIBRARY_ONLY} | |", f"| {LIBRARY_ONLY} | {comments.NOT_A_LIBRARY} |")
    text = PUBLISHED_PLACEHOLDER.sub(f"- {comments.PUBLISHED}: {comments.NOT_A_LIBRARY}", text)
    text = COMMENTS_PLACEHOLDER.sub(SELF_TEST_COMMENTS, text)
    text = DOCUMENTS_PLACEHOLDER.sub(SELF_TEST_DOCUMENTS, text)
    for question, proven in application_types.load().proofs.items():
        if FORMAL_PROOF in proven:
            trigger = application_types.load().trigger_of(question)
            text = text.replace(f"| {question} | |", f"| {question} | later: {trigger} |")
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
    text = REPORTS_PLACEHOLDER.sub(SELF_TEST_REPORTS, text)
    text = SUITES_PLACEHOLDER.sub(SELF_TEST_SUITES, text)
    text = EMPTY_TOOL_CELL.sub(r"\1 a tool |", text)
    return SPEED_PLACEHOLDER.sub(r"\1stated for this stack", text)


def triggers_rows(_: re.Match[str]) -> str:
    kinds = sorted(application_types.load().kinds_required({"cli"}))
    return "\n".join(f"| {kind} | none: the self-test has none | |" for kind in kinds)


def suppression_rows(_: re.Match[str]) -> str:
    return "\n".join(
        f"| {question} | `{SELF_TEST_KIND}` |"
        for question in sorted(application_types.load().directed)
    )


def fill_registry(binding: str) -> str:
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
    path = root / "guards/manifest.toml"
    path.write_text(UNBOUND_GROUP.sub('paths = [\n    "bound/**",\n]', path.read_text()))
    settings_rules.regenerate(root)
    binding = root / BINDING_PATH
    binding.write_text(fill_binding(binding.read_text()))
    registry = root / project_bite.REGISTRY
    registry.write_text(registry.read_text() + fill_registry(binding.read_text()))
    (root / SELF_TEST_RECORD).write_text(SELF_TEST_ARCHITECTURE)
    (root / "collection").mkdir(exist_ok=True)
    (root / "collection/case").write_text("the collection a remove bite empties\n")
    pinned = {PLUGIN_CHECKOUT_PLACEHOLDER: "0" * 40, TOOLCHAIN_PLACEHOLDER: TOOLCHAIN_STEP}
    fills = {
        "GLOSSARY.md": {GLOSSARY_PLACEHOLDER: "the project"},
        str(WORKFLOWS / "ci.yml"): pinned,
        str(WORKFLOWS / "deep.yml"): pinned,
        str(WORKFLOWS / MERGE_WORKFLOW): pinned,
        str(WORKFLOWS / WEEKLY_WORKFLOW): pinned,
    }
    justfile = root / "justfile"
    justfile.write_text(
        INCREMENTAL_PLACEHOLDER.sub('"stated for this stack"', justfile.read_text())
    )
    for name, replacements in fills.items():
        target = root / name
        text = LATER_SETUP_PLACEHOLDER.sub("", target.read_text())
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
