#!/usr/bin/env python3
"""Every shipped gate against its synthetic violation: each must go red, and green without it.

A gate nobody has seen fail is a gate that may never fire. For each gate this plugin ships,
``bite.py`` builds a scratch repository on ``main`` with one commit, applies a violation on a
branch, runs the gate and expects red; then runs it on the clean state and expects green. The
adopted repository's ``bite-all`` entry point runs this, and the plugin's own ``just check``
runs it too. ``project_bite.py`` is the other half: the gates a repository bound to its own tools,
against the faults its ``quality/bites.toml`` plants. It is a shipped gate, so it is bitten here.

Usage: ``bite.py``. Exits 1 when any gate fails to bite or fails to pass.
"""

from __future__ import annotations

import http.server
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import budget
import change_size
import commits
import conformance
import coverage_groups
import drain
import feature_preflight
import flags
import hooks_installed
import load_smoke
import lowered
import markers
import merge_ready
import mutation_survivors
import parallel
import pins
import plugin_pin
import project_bite
import prose
import settings_rules
import snapshots
import suppressions
import telemetry_sink
import triggers
import with_service

# Why: the guard-diff gate lives with the guard, under hooks/, as gatelib lives here for it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import diff as guard_paths

Gate = Callable[[list[str]], int]
Violation = Callable[[Path], None]
WORKFLOW = ".github/workflows/ci.yml"
PINNED = (
    "name: ci\non: [push]\npermissions:\n  contents: read\njobs:\n  a:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5\n"
)
GLOSSARY = "# Glossary\n\n## Language\n\n**Customer**:\nWho buys.\n_Avoid_: client, buyer\n"
MANIFEST = (
    '[[group]]\nname = "tool-config"\nreason = "Rule sets of every gate."\n'
    'paths = ["lint.toml"]\n\n'
    '[[group]]\nname = "harness"\nreason = "Settings enforce the guards."\nedit = "deny"\n'
    'paths = [".claude/settings.json"]\n\n'
    '[[group]]\nname = "records"\nreason = "An agent adds a term; the owner changes one."\n'
    'edit = "append"\npaths = ["GLOSSARY.md", "docs/adr/**"]\n\n'
    '[[door]]\nname = "contracts"\nreason = "A consumer depends on every line."\n'
    'on = "removal"\npaths = ["surface.txt"]\n'
)
THRESHOLDS = (
    '[python.coverage.core]\npaths = ["src/**"]\nabsolute = true\n\n'
    "[ceilings.change]\nproduct_files = 1\nproduct_lines = 2\ntest_lines = 100\n"
)
# Why: the preflight reads a local ticket only where the guard asks about its label.
TICKET_PATH = ".scratch/thing/issues/01-add-the-thing.md"
TICKET = (
    "# 01: Add the thing\n\n**Status:** `ready-for-agent`\n\n## What to build\n\nThe thing.\n\n"
    "## Conjecture\n\nOne function in src/a.py; the refuter is a failing call with two operands."
    " Alternatives: a class (killed: nothing holds state).\n"
)
FLAG = (
    '[[flag]]\nkey = "new-sum"\nowner = "core"\ndefault = {default}\n'
    'remove-by = {due}\nreason = "Ships the new sum dark until it is measured."\n'
)
# A repository's own two gates, as one shell script, and the faults that show each one bites.
LINT_RULE = 'if grep -rq planted src; then echo "L001 a planted word"; exit 1; fi'
TESTS_RULE = 'ls tests/test_*.py >/dev/null 2>&1 || { echo "no test to run"; exit 1; }'
OWN_GATES = f'#!/bin/sh\ncase "$1" in\nlint) {LINT_RULE} ;;\ntests) {TESTS_RULE} ;;\nesac\n'
LINT_BITE = (
    '[[bite]]\ngate = "lint"\nname = "a planted word"\n'
    'write = { path = "src/b.py", content = "planted = 1\\n" }\nexpect = "L001"\n'
)
TESTS_BITE = (
    '[[bite]]\ngate = "tests"\nname = "no test"\nremove = ["tests/**"]\nexpect = "no test to run"\n'
)
BINDING = (
    "# Binding\n\n- Default branch: `main`\n- Life events: deployed\n"
    f"- Ratchet plugin: 1.0.0 at {'0' * 40}\n\n## Gates\n\n"
    "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
    "| lint | a linter | 0 | | `sh own-gates.sh lint` |\n"
    "| unit, property and fitness tests | a test runner | 1 | | `sh own-gates.sh tests` |\n"
)
SUPPRESSIONS = (
    "\n## Suppressions\n\n| Kind | Pattern | Rule |\n| --- | --- | --- |\n"
    "| bite-silence | `bite-silence` | rises |\n| bite-focus | `bite-focus` | never |\n\n"
    "| Question | Kinds |\n| --- | --- |\n| lint | `bite-silence` |\n"
)
HOOK = '#!/bin/sh\n[ -n "$RATCHET_HOOK_PROBE" ] && echo {name} > "$RATCHET_HOOK_PROBE"\nexit 0\n'
RECORDS = (
    '{{"file": "src/a.py", "mutator": "operator", "replacement": "-", "status": "{status}",'
    ' "start": [2, 14], "end": [2, 15]}}\n'
)
LCOV = "SF:src/a.py\nDA:1,1\nDA:2,{hits}\nend_of_record\n"
IN_LCOV = ["--report", "coverage.info"]
REGISTER_PATH = "quality/mutation-register.toml"
REGISTER = "# Mutation register\n\n{entry}\n"
REPORT = (
    '<?xml version="1.0"?><coverage><sources><source>{root}</source></sources><packages>'
    '<package><classes><class filename="src/a.py"><lines>'
    '<line number="1" hits="1"/><line number="2" hits="{hits}" branch="true" '
    'condition-coverage="{cond}% (1/{total})"/></lines></class></classes></package>'
    "</packages></coverage>"
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def scratch(root: Path) -> None:
    """A repository on ``main`` with one clean commit carrying every gate's inputs."""
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "bite@example.invalid")
    git(root, "config", "user.name", "bite")
    git(root, "config", "commit.gpgsign", "false")
    write(root, "src/a.py", "def f(a, b):\n    return a + b\n")
    write(root, "lint.toml", "strict = true\n")
    write(root, "README.md", "# A\n\nThe customer pays.\n")
    write(root, "GLOSSARY.md", GLOSSARY)
    write(root, "guards/manifest.toml", MANIFEST)
    write(root, "quality/thresholds.toml", THRESHOLDS)
    write(root, WORKFLOW, PINNED)
    write(root, "coverage.xml", REPORT.format(root=root, hits=1, cond=100, total=1))
    write(root, "coverage.info", LCOV.format(hits=1))
    write(root, "mutants.jsonl", RECORDS.format(status="killed"))
    write(root, REGISTER_PATH, "# Mutation register\n")
    write(root, TICKET_PATH, TICKET)
    write(root, "flags.toml", "# No flags yet.\n")
    write(root, "surface.txt", "f(a, b)\n")
    write(root, "docs/process/binding.md", BINDING + SUPPRESSIONS)
    write(root, "own-gates.sh", OWN_GATES)
    write(root, PLUGIN_MANIFEST, '{"version": "1.0.0"}\n')
    write(root, "tests/test_a.py", "def test_f():\n    assert True\n")
    write(root, str(project_bite.REGISTRY), LINT_BITE + TESTS_BITE)
    install_hooks(root)
    settings_rules.regenerate(root)
    snapshots.write(root, snapshots.GROUP)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat(core): the clean state")
    git(root, "checkout", "-q", "-b", "change")


def install_hooks(root: Path) -> None:
    """Hooks that answer the probe as the template's recipes do: a commit here would run tier 0."""
    for name in hooks_installed.HOOKS:
        hook = root / ".git/hooks" / name
        hook.write_text(HOOK.format(name=name), encoding="utf-8")
        hook.chmod(0o755)


def commit(root: Path, subject: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", subject)


def lint_green_on_its_fault(root: Path) -> None:
    write(root, "own-gates.sh", OWN_GATES.replace(LINT_RULE, "true"))


def tests_green_on_nothing(root: Path) -> None:
    write(root, "own-gates.sh", OWN_GATES.replace(TESTS_RULE, "true"))


def bite_dropped(root: Path) -> None:
    write(root, str(project_bite.REGISTRY), TESTS_BITE)


def add_marker(root: Path) -> None:
    write(root, "src/a.py", "def f(a, b):\n    return a + b  # TODO later\n")


def add_suppression(root: Path) -> None:
    write(root, "src/a.py", "def f(a, b):\n    return a + b  # bite-silence\n")


def survivor(root: Path) -> None:
    write(root, "mutants.jsonl", RECORDS.format(status="survived"))


def nothing_but_unviable_mutants(root: Path) -> None:
    write(root, "mutants.jsonl", RECORDS.format(status="unviable"))


def entry(tool: str, identity: str, function_hash: str) -> str:
    return (
        f'[[survivor]]\ntool = "{tool}"\nidentity = "{identity}"\n'
        f'function_hash = "{function_hash}"\n'
        'reason = "Equivalent: the operands commute at every call site."\ndate = 2026-11-07\n'
    )


def stale_entry(root: Path) -> None:
    identity = "src/a.py:operator:-@1:20-1:25"
    digest = mutation_survivors.unit_hash(["def f(a, b):", "    return a + b"], 2)
    write(root, REGISTER_PATH, REGISTER.format(entry=entry(TOOL, identity, digest)))


def records(root: Path, *statuses: str) -> list[mutation_survivors.Mutant]:
    """The neutral records with one mutant per status, all of one mutator and replacement."""
    lines = [
        json.dumps(
            {
                "file": "src/a.py",
                "mutator": "operator",
                "replacement": "-",
                "status": status,
                "start": [2, 10 * n],
                "end": [2, 10 * n + 5],
            }
        )
        for n, status in enumerate(statuses, 1)
    ]
    text = "\n".join(lines) + "\n"
    write(root, "mutants.jsonl", text)
    return mutation_survivors.neutral_mutants(text, root)[0]


def register_survivors(root: Path, *identities: tuple[str, str]) -> None:
    write(
        root,
        REGISTER_PATH,
        REGISTER.format(entry="\n".join(entry(TOOL, *pair) for pair in identities)),
    )


def twin_survivors(root: Path) -> None:
    first, _ = records(root, "survived", "survived")
    register_survivors(root, (first.identity, first.function_hash))


def entry_without_position(root: Path) -> None:
    (only,) = records(root, "survived")
    old = mutation_survivors.POSITION.sub("", only.identity)
    register_survivors(root, (old, only.function_hash))


def timed_out_entry(root: Path) -> None:
    (only,) = records(root, "survived")
    register_survivors(root, (only.identity, only.function_hash))
    records(root, "timeout")


def no_conjecture(root: Path) -> None:
    write(root, TICKET_PATH, TICKET.replace("## Conjecture", "## Notes"))
    commit(root, "docs(tickets): drop the conjecture")


def not_agent_ready(root: Path) -> None:
    write(root, TICKET_PATH, TICKET.replace("ready-for-agent", "needs-info"))
    commit(root, "docs(tickets): back to triage")


def flag_born_on(root: Path) -> None:
    write(root, "flags.toml", FLAG.format(default="true", due="2999-01-01"))


def flag_overdue(root: Path) -> None:
    write(root, "flags.toml", FLAG.format(default="false", due="2000-01-01"))


def add_focus(root: Path) -> None:
    write(root, "src/a.test.js", "// bite-focus\n")


def unpin(root: Path) -> None:
    write(root, WORKFLOW, PINNED.replace("@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5", "@v5"))


def avoided_word(root: Path) -> None:
    write(root, "README.md", "# A\n\nThe client pays.\n")


def change_config(root: Path) -> None:
    write(root, "lint.toml", "strict = false\n")


def two_files(root: Path) -> None:
    write(root, "src/b.py", "y = 1\ny = 2\n")
    write(root, "src/c.py", "z = 1\n")
    commit(root, "feat(core): two files")


def bad_subject(root: Path) -> None:
    commit(root, "fixed stuff.")


def uncovered(root: Path) -> None:
    write(root, "coverage.xml", REPORT.format(root=root, hits=0, cond=50, total=2))


def uncovered_in_lcov(root: Path) -> None:
    write(root, "coverage.info", LCOV.format(hits=0))


def lowered_threshold(root: Path) -> None:
    write(root, "quality/thresholds.toml", THRESHOLDS.replace("lines = 2", "lines = 3"))
    commit(root, "chore(quality): raise the ceiling")


def remove_hooks(root: Path) -> None:
    for name in hooks_installed.HOOKS:
        (root / ".git/hooks" / name).unlink()


def hooks_path_elsewhere(root: Path) -> None:
    git(root, "config", "core.hooksPath", str(root / "no-hooks"))


def one_gate_goes_red(root: Path) -> None:
    write(root, "red", "")


def guard_diff(argv: list[str]) -> int:
    return guard_paths.main(argv[1:])


def drop_avoid_item(root: Path) -> None:
    added = "\n**Order**:\nWhat a customer buys.\n_Avoid_: basket\n"
    write(root, "GLOSSARY.md", GLOSSARY.replace("client, buyer", "client") + added)


def foreign_deny(root: Path) -> None:
    path = root / ".claude/settings.json"
    path.write_text(
        path.read_text().replace('"deny": [', '"deny": [\n      "Bash(gh label create:*)",')
    )


DONE: dict[str, list[str]] = {"unresolved": [], "openQuestions": [], "assumptions": []}
VERDICT = {"slug": "the-thing", "ticket": "01", "verdict": "approve", **DONE}
VERDICT_RECORD = "docs/features/the-thing/verdict.json"


LATER_ROW = "| survives failing dependencies | later: code:outbound-call | 1 | | `just faults` |\n"
TRIGGERS_TABLE = (
    "\n## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n"
    "| outbound-call | `src/**/*.py` | `requests\\.get\\(` |\n"
)
OUTBOUND_CALL = "import requests\nrequests.get('http://x')\n"


def triggers_gate(argv: list[str]) -> int:
    """The triggers gate on a repository whose base already holds one outbound call.

    The first one is free, so the scratch repository is first given the call, committed, with the
    row still ``later:``; the change is judged against that commit.
    """
    root = Path(argv[2])
    if not (root / "src/client.py").is_file():
        write(root, "docs/process/binding.md", BINDING + LATER_ROW + TRIGGERS_TABLE + SUPPRESSIONS)
        write(root, "src/client.py", OUTBOUND_CALL)
        commit(root, "feat(core): the first outbound call")
    return triggers.main([*argv, "--base", "HEAD"])


def second_outbound_call(root: Path) -> None:
    write(root, "src/other.py", OUTBOUND_CALL)


def incidents_gate(argv: list[str]) -> int:
    """The incidents check of the conformance gate, alone."""
    return conformance.main(["conformance", "--incidents", argv[2]])


def incident_without_a_bite(root: Path) -> None:
    write(root, "docs/incidents/2026-01-01-outage.md", "# The outage\n\nIt went down.\n")


def merge_judge(argv: list[str]) -> int:
    """The merge judge on the scratch change as the pipeline leaves one: committed, then recorded.

    Work not yet committed is committed and the verdict recorded over it, so the judge is red for
    what the change does and never for a missing record. A commit made after the record stays as
    it is: that is the violation.
    """
    root = Path(argv[2])
    if merge_ready.dirty(root, VERDICT_RECORD) or not (root / VERDICT_RECORD).is_file():
        write(root, "src/b.py", "B = 1\n")
        commit(root, "feat(core): the change")
        merge_ready.write_record(root, json.dumps(VERDICT))
        commit(root, "docs(core): the verdict record")
    return merge_ready.main([*argv, "--base", "main"])


PLUGIN_COPY = "plugin"
PLUGIN_MANIFEST = f"{PLUGIN_COPY}/{plugin_pin.MANIFEST}"


def pin_gate(argv: list[str]) -> int:
    """The pin check against the plugin copy the scratch repository carries."""
    return plugin_pin.main([*argv, "--plugin", str(Path(argv[2]) / PLUGIN_COPY)])


def plugin_older_than_the_pin(root: Path) -> None:
    write(root, PLUGIN_MANIFEST, '{"version": "0.9.0"}\n')


def tier_ceiling_passed(root: Path) -> None:
    path = root / "quality/thresholds.toml"
    path.write_text(budget.with_ceiling(path.read_text(), "tier1", 0.0))


def commit_after_the_review(root: Path) -> None:
    write(root, "src/b.py", "B = 2\n")
    commit(root, "fix(core): one more thing")


def new_surface(root: Path) -> None:
    write(root, "surface.txt", "f(a, b)\ng(a)\n")


QUEUED = ".scratch/the-thing/issues/01-add-the-thing.md"
BLOCKER = ".scratch/the-thing/issues/02-first.md"


def queue_gate(argv: list[str]) -> int:
    """``drain.py open`` on a ticket of the default branch; the work tree it makes is removed."""
    root = Path(argv[2])
    if not (root / QUEUED).is_file():
        git(root, "switch", "-q", "main")
        write(root, QUEUED, TICKET)
        commit(root, "docs(tracker): the ticket")
    opened = drain.main(["drain", "open", QUEUED, "--root", str(root), "--offline"])
    drain.main(["drain", "clean", "--all", "--root", str(root)])
    return opened


def behind_an_open_blocker(root: Path) -> None:
    write(root, QUEUED, TICKET.replace("**Status:**", "Blocked by: 02\n\n**Status:**"))
    write(root, BLOCKER, "# 02: First\n\n**Status:** `needs-info`\n")
    commit(root, "docs(tracker): a blocker the owner has not agreed")


TOOL = "any-tool"
ANY_TOOL = ["--tool", TOOL, "--mutants", "mutants.jsonl"]
TICKET_REF = [TICKET_PATH]
ADDITIVE = ["--fail-unless-additive"]
GATE_READS_RED = "import pathlib, sys; sys.exit(pathlib.Path('red').is_file())"
PARALLEL = [
    *("--runner", f"{shlex.quote(sys.executable)} -c", "--jobs", "2"),
    *("--", "pass", shlex.quote(GATE_READS_RED), "0"),
]
IN_TIER = ["lint", "--tier", "tier1", "--", sys.executable, "-c", "pass"]
SCENARIOS: list[tuple[str, Gate, Violation, list[str]]] = [
    ("markers", markers.main, add_marker, []),
    ("suppressions: a count rose", suppressions.main, add_suppression, []),
    ("suppressions: a focus marker", suppressions.main, add_focus, []),
    ("pins", pins.main, unpin, []),
    ("prose", prose.main, avoided_word, []),
    ("snapshots-check", snapshots.main, change_config, ["--check"]),
    ("change-size", change_size.main, two_files, []),
    ("commits", commits.main, bad_subject, []),
    ("coverage", coverage_groups.main, uncovered, []),
    ("coverage: an LCOV report", coverage_groups.main, uncovered_in_lcov, IN_LCOV),
    ("mutation: a survivor", mutation_survivors.main, survivor, ANY_TOOL),
    ("mutation: a stale entry", mutation_survivors.main, stale_entry, ANY_TOOL),
    (
        "mutation: no mutant was tested",
        mutation_survivors.main,
        nothing_but_unviable_mutants,
        ANY_TOOL,
    ),
    ("mutation: two survivors, one entry", mutation_survivors.main, twin_survivors, ANY_TOOL),
    (
        "mutation: an entry without a position",
        mutation_survivors.main,
        entry_without_position,
        ANY_TOOL,
    ),
    (
        "mutation: a registered survivor that timed out",
        mutation_survivors.main,
        timed_out_entry,
        ANY_TOOL,
    ),
    ("ratchet-lowered", lowered.main, lowered_threshold, []),
    ("feature-preflight: no conjecture", feature_preflight.main, no_conjecture, TICKET_REF),
    ("feature-preflight: not agent-ready", feature_preflight.main, not_agent_ready, TICKET_REF),
    ("settings-check", settings_rules.main, foreign_deny, ["--check"]),
    ("guard-diff: a record loses an Avoid item", guard_diff, drop_avoid_item, ADDITIVE),
    ("flags: a new flag that is on", flags.main, flag_born_on, []),
    ("flags-expired", flags.main, flag_overdue, ["--expired"]),
    ("hooks-installed: no hooks", hooks_installed.main, remove_hooks, []),
    ("hooks-installed: a hooks path elsewhere", hooks_installed.main, hooks_path_elsewhere, []),
    ("feature-preflight: no hooks", feature_preflight.main, remove_hooks, TICKET_REF),
    ("parallel: a red gate among green ones", parallel.main, one_gate_goes_red, PARALLEL),
    ("project-bite: a gate green on its fault", project_bite.main, lint_green_on_its_fault, []),
    ("project-bite: a gate green on nothing to run", project_bite.main, tests_green_on_nothing, []),
    ("project-bite: a bound gate with no bite", project_bite.main, bite_dropped, []),
    ("triggers: a second instance of an owed kind", triggers_gate, second_outbound_call, []),
    ("incidents: a record that names no bite", incidents_gate, incident_without_a_bite, []),
    ("merge-ready: a guard path changed", merge_judge, change_config, []),
    ("merge-ready: a flag on by default", merge_judge, flag_born_on, []),
    ("merge-ready: a commit after the review", merge_judge, commit_after_the_review, []),
    ("merge-ready: new surface with no flag", merge_judge, new_surface, []),
    ("queue: a ticket behind an open blocker", queue_gate, behind_an_open_blocker, []),
    ("plugin-pin: a plugin older than the pin", pin_gate, plugin_older_than_the_pin, []),
    ("budget: a gate over its tier's ceiling", budget.main, tier_ceiling_passed, IN_TIER),
]


SERVE = (
    "import os, http.server as h; "
    "h.ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), "
    "h.SimpleHTTPRequestHandler).serve_forever()"
)
EXPORT = (
    "import json, os, urllib.request as u; "
    "body = {'resourceSpans': [{'resource': {'attributes': [{'key': 'service.name', "
    "'value': {'stringValue': 'bite'}}]}, 'scopeSpans': [{'spans': [{}]}]}]}; "
    "u.urlopen(u.Request(os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] + '/v1/traces', "
    "json.dumps(body).encode(), {'Content-Type': 'application/json'})).close()"
)


def python(code: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


STOPS = (
    "import os, signal, threading, time, http.server as h\n"
    "class S(h.ThreadingHTTPServer):\n"
    "    daemon_threads = False\n"
    "    block_on_close = True\n"
    "    def handle_error(self, request, client_address): pass\n"
    "class H(h.BaseHTTPRequestHandler):\n"
    "    def log_message(self, *args): pass\n"
    "    def do_GET(self):\n"
    "        time.sleep(0.3)\n"
    "        self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()\n"
    "s = S(('127.0.0.1', int(os.environ['PORT'])), H)\n"
    "def stop(*args):\n"
    "    if os.environ['DROPS']: os._exit(0)\n"
    "    threading.Thread(target=s.shutdown).start()\n"
    "signal.signal(signal.SIGTERM, stop)\n"
    "s.serve_forever(); s.server_close()"
)
# The held command: the load smoke, its own report kept out of this gate's output.
HELD = (
    "import subprocess, sys; sys.exit(subprocess.run(sys.argv[1:], capture_output=True).returncode)"
)
LOAD_SMOKE = str(Path(__file__).with_name("load_smoke.py"))
GRACEFUL = [
    *("--ready", "http://127.0.0.1:{port}/", "--graceful", "3", "--signal-after", "0.5"),
    *("--", sys.executable, "-c", HELD, sys.executable, LOAD_SMOKE),
    *("--url", "http://127.0.0.1:{port}/work", "--until-stopped", "--seconds", "2"),
    *("--concurrency", "2", "--min-requests", "1"),
]
REFUSES = "import os, sys; 'BITE_SETTING' in os.environ or sys.exit('BITE_SETTING is not set')\n"

READY = "http://127.0.0.1:{port}/"
LOAD = ["--ready", "{stub}/health", "--seconds", "0.5", "--concurrency", "2", "--min-requests", "1"]
SINK = ["--service", "bite", "--require", "traces"]
SERVICE_SCENARIOS: list[tuple[str, Gate, list[str], list[str]]] = [
    (
        "smoke: a service that never answers",
        with_service.main,
        # Why: the clean run returns the moment the service answers; the limit is only how long a
        # starting interpreter may take on a machine running many of them at once.
        ["--boot", python(SERVE), "--ready", READY, "--timeout", "120"],
        ["--boot", python("import time; time.sleep(30)"), "--ready", READY, "--timeout", "0.5"],
    ),
    (
        "smoke: a service that drops requests in flight when stopped",
        with_service.main,
        ["--boot", f"DROPS= exec {python(STOPS)}", *GRACEFUL],
        ["--boot", f"DROPS=1 exec {python(STOPS)}", *GRACEFUL],
    ),
    (
        "smoke: a service that boots without a required setting",
        with_service.main,
        ["--boot", python(REFUSES + SERVE), "--ready", READY, "--refuses", "BITE_SETTING"],
        ["--boot", python(SERVE), "--ready", READY, "--refuses", "BITE_SETTING"],
    ),
    (
        "load-smoke: a 500 under load",
        load_smoke.main,
        ["--url", "{stub}/ok", *LOAD],
        ["--url", "{stub}/boom", *LOAD],
    ),
    (
        "load-smoke: nothing but the readiness URL",
        load_smoke.main,
        ["--url", "{stub}/ok", *LOAD],
        ["--url", "{stub}/health", *LOAD],
    ),
    (
        "telemetry-sink: no telemetry",
        telemetry_sink.main,
        [*SINK, "--wait", "5", "--", sys.executable, "-c", EXPORT],
        [*SINK, "--wait", "0.3", "--", sys.executable, "-c", ""],
    ),
]


@contextmanager
def stub_service() -> Iterator[str]:
    """A loopback server that answers 200, and 500 on ``/boom``; its base URL."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(500 if self.path == "/boom" else 200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
            """Say nothing."""

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def quietly(gate: Gate, argv: list[str]) -> tuple[int, str]:
    """The gate's exit code and what it printed."""
    said = io.StringIO()
    with redirect_stdout(said), redirect_stderr(said):
        code = gate(argv)
    return code, said.getvalue()


def run_service(gate: Gate, arguments: list[str], stub: str) -> tuple[int, str]:
    return quietly(gate, ["gate", *(argument.replace("{stub}", stub) for argument in arguments)])


def run(gate: Gate, root: Path, flags: list[str]) -> tuple[int, str]:
    return quietly(gate, ["gate", "--root", str(root), *flags])


def verdict(label: str, clean: tuple[int, str], faulty: tuple[int, str]) -> bool:
    """Print the scenario's line; whether the gate bit and passed.

    A gate red on the clean state prints what it said, so the red explains itself.
    """
    green, red = clean[0] == 0, faulty[0] != 0
    text = "bites" if red and green else "DOES NOT BITE" if green else "RED ON CLEAN STATE"
    print(f"bite: {label}: {text}")
    if not green:
        for line in clean[1].splitlines():
            print(f"  {line}")
    return red and green


def bite(scratch_dir: Path) -> int:
    """Every scenario, with the machine's git config set aside.

    A hooks path in it would change which hooks a scratch repository runs.
    """
    with mock.patch.dict(
        os.environ,
        {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    ):
        os.environ.pop("RATCHET_HOOK_PROBE", None)
        # Why: run as a gate inside a scratch copy, this script still bites the runner itself.
        os.environ.pop(project_bite.BITING, None)
        return scenarios(scratch_dir)


def scenarios(scratch_dir: Path) -> int:
    failures = 0
    for label, gate, violate, options in SCENARIOS:
        root = scratch_dir / label.replace(" ", "-").replace(":", "")
        root.mkdir()
        scratch(root)
        clean = run(gate, root, options)
        violate(root)
        failures += not verdict(label, clean, run(gate, root, options))
    with stub_service() as url:
        for label, gate, healthy, violating in SERVICE_SCENARIOS:
            clean = run_service(gate, healthy, url)
            failures += not verdict(label, clean, run_service(gate, violating, url))
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    del argv
    with tempfile.TemporaryDirectory() as scratch_dir:
        return bite(Path(os.path.realpath(scratch_dir)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
