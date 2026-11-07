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
from contextlib import redirect_stdout
from pathlib import Path

import change_size
import commits
import coverage_groups
import lowered
import markers
import mutation_survivors
import pins
import prose
import settings_rules
import snapshots
import suppressions

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
    'paths = [".claude/settings.json"]\n'
)
THRESHOLDS = (
    '[python.coverage.core]\npaths = ["src/**"]\nabsolute = true\n\n'
    "[ceilings.change]\nfiles = 1\nlines = 2\n"
)
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
    settings_rules.write(root)
    snapshots.write(root, snapshots.GROUP)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat(core): the clean state")
    git(root, "checkout", "-q", "-b", "change")


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


def foreign_deny(root: Path) -> None:
    path = root / ".claude/settings.json"
    path.write_text(
        path.read_text().replace('"deny": [', '"deny": [\n      "Bash(gh label create:*)",')
    )


MUTMUT = ["--tool", "mutmut", "--results", "mutants.txt"]
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
    ("settings-check", settings_rules.main, foreign_deny, ["--check"]),
]


def run(gate: Gate, root: Path, flags: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return gate(["gate", "--root", str(root), *flags])


def bite(scratch_dir: Path) -> int:
    failures = 0
    for label, gate, violate, flags in SCENARIOS:
        root = scratch_dir / label.replace(" ", "-").replace(":", "")
        root.mkdir()
        scratch(root)
        green = run(gate, root, flags) == 0
        violate(root)
        red = run(gate, root, flags) != 0
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
