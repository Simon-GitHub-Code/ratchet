#!/usr/bin/env python3
"""Every shipped gate against its synthetic violation: each must go red, and green without it.

A gate nobody has seen fail is a gate that may never fire. For each gate this plugin ships,
``bite.py`` builds a scratch repository on ``main`` with one commit, applies a violation on a
branch, runs the gate and expects red; then runs it on the clean state and expects green. The
adopted repository's ``bite`` entry point runs this beside its own fitness tests' self-tests,
and the plugin's own ``just check`` runs it too.

Usage: ``bite.py``. Exits 1 when any gate fails to bite or fails to pass.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import change_size
import commits
import coverage_groups
import feature_preflight
import flags
import hooks_installed
import lowered
import markers
import mutation_survivors
import pins
import prose
import settings_rules
import snapshots
import suppressions

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
    'edit = "append"\npaths = ["GLOSSARY.md", "docs/adr/**"]\n'
)
THRESHOLDS = (
    '[python.coverage.core]\npaths = ["src/**"]\nabsolute = true\n\n'
    "[ceilings.change]\nfiles = 1\nlines = 2\n"
)
TICKET = (
    "# 01: Add the thing\n\n**Status:** `ready-for-agent`\n\n## What to build\n\nThe thing.\n\n"
    "## Conjecture\n\nOne function in src/a.py; the refuter is a failing call with two operands."
    " Alternatives: a class (killed: nothing holds state).\n"
)
FLAG = (
    '[[flag]]\nkey = "new-sum"\nowner = "core"\ndefault = {default}\n'
    'remove-by = {due}\nreason = "Ships the new sum dark until it is measured."\n'
)
BINDING = "# Binding\n\n- Default branch: `main`\n"
HOOK = '#!/bin/sh\n[ -n "$RATCHET_HOOK_PROBE" ] && echo {name} > "$RATCHET_HOOK_PROBE"\nexit 0\n'
RESULTS = "    a.x_f__mutmut_1: killed\n    a.x_f__mutmut_2: {status}\n"
REGISTER = (
    '# Mutation register\n\n[[survivor]]\ntool = "mutmut"\nidentity = "a.x_f__mutmut_1"\n'
    'function_hash = "{function_hash}"\n'
    'reason = "Equivalent: the operands commute at every call site."\ndate = 2026-11-07\n'
)
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
    write(root, "mutants.txt", RESULTS.format(status="killed"))
    write(root, "quality/mutation-register.toml", "# Mutation register\n")
    write(root, "tickets/01-add-the-thing.md", TICKET)
    write(root, "flags.toml", "# No flags yet.\n")
    write(root, "docs/process/binding.md", BINDING)
    install_hooks(root)
    settings_rules.write(root)
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


def add_marker(root: Path) -> None:
    write(root, "src/a.py", "def f(a, b):\n    return a + b  # TODO later\n")


def add_suppression(root: Path) -> None:
    write(root, "src/a.py", "def f(a, b):\n    return a + b  # noqa: E501\n")


def survivor(root: Path) -> None:
    write(root, "mutants.txt", RESULTS.format(status="survived"))


def stale_entry(root: Path) -> None:
    function_hash = mutation_survivors.python_function_hash(root, "a", "x_f")
    write(root, "quality/mutation-register.toml", REGISTER.format(function_hash=function_hash))


def no_conjecture(root: Path) -> None:
    write(root, "tickets/01-add-the-thing.md", TICKET.replace("## Conjecture", "## Notes"))
    commit(root, "docs(tickets): drop the conjecture")


def not_agent_ready(root: Path) -> None:
    write(root, "tickets/01-add-the-thing.md", TICKET.replace("ready-for-agent", "needs-info"))
    commit(root, "docs(tickets): back to triage")


def flag_born_on(root: Path) -> None:
    write(root, "flags.toml", FLAG.format(default="true", due="2999-01-01"))


def flag_overdue(root: Path) -> None:
    write(root, "flags.toml", FLAG.format(default="false", due="2000-01-01"))


def add_focus(root: Path) -> None:
    write(root, "src/a.test.js", "it.only('x', () => {})\n")


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


def lowered_threshold(root: Path) -> None:
    write(root, "quality/thresholds.toml", THRESHOLDS.replace("lines = 2", "lines = 3"))
    commit(root, "chore(quality): raise the ceiling")


def remove_hooks(root: Path) -> None:
    for name in hooks_installed.HOOKS:
        (root / ".git/hooks" / name).unlink()


def hooks_path_elsewhere(root: Path) -> None:
    git(root, "config", "core.hooksPath", str(root / "no-hooks"))


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


MUTMUT = ["--tool", "mutmut", "--results", "mutants.txt"]
TICKET_REF = ["tickets/01-add-the-thing.md"]
ADDITIVE = ["--fail-unless-additive"]
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
    ("mutation: a survivor", mutation_survivors.main, survivor, MUTMUT),
    ("mutation: a stale entry", mutation_survivors.main, stale_entry, MUTMUT),
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
]


def run(gate: Gate, root: Path, flags: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return gate(["gate", "--root", str(root), *flags])


def bite(scratch_dir: Path) -> int:
    """Every scenario, with the machine's git config set aside.

    A hooks path in it would change which hooks a scratch repository runs.
    """
    with mock.patch.dict(
        os.environ,
        {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    ):
        os.environ.pop("RATCHET_HOOK_PROBE", None)
        return scenarios(scratch_dir)


def scenarios(scratch_dir: Path) -> int:
    failures = 0
    for label, gate, violate, options in SCENARIOS:
        root = scratch_dir / label.replace(" ", "-").replace(":", "")
        root.mkdir()
        scratch(root)
        green = run(gate, root, options) == 0
        violate(root)
        red = run(gate, root, options) != 0
        verdict = "bites" if red and green else "DOES NOT BITE" if green else "RED ON CLEAN STATE"
        print(f"bite: {label}: {verdict}")
        failures += not (red and green)
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    del argv
    with tempfile.TemporaryDirectory() as scratch_dir:
        return bite(Path(os.path.realpath(scratch_dir)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
