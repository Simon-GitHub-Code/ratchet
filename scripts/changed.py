#!/usr/bin/env python3
"""Run a gate over the change against its base, or say the change is the adoption.

A gate over the change (coverage of changed lines, mutation of changed functions, the test count
against the base, the flag states of changed flags) compares the tree with a base: the merge
base with the default branch, or on that branch the commit before. This wrapper hands the gate
that commit, so every such gate and every shipped one agree on what the change is.

The adoption is the one change with no base to compare with. Its base holds no binding and no
suite, so against it every line is changed, and each such gate would repeat on every run what
tier 2 does once over the whole tree: the full mutation run, the coverage groups, ``bite-all``.
So on the adoption the wrapper runs nothing, says so and exits 0. That is decided from the base
commit, which no later change can make true again: once the adoption has merged, every base holds
a binding.

In a scratch copy of the bite proof the gate always runs, since its fault is the change.

Usage: ``changed.py [--root DIR] -- COMMAND...``. ``{base}`` in any word of ``COMMAND`` becomes
the base commit, which is also exported as ``RATCHET_BASE``. Exits with the command's code, 0 on
the adoption, 127 when the command cannot start and 2 for a usage error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import gatelib

# Why: `project_bite.py` sets this in a scratch copy, where the planted fault is the change.
BITING = "RATCHET_BITING"
BASE_ENV = "RATCHET_BASE"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="changed.py", usage="%(prog)s [--root DIR] -- COMMAND...")
    parser.add_argument("--root", type=Path, default=Path())
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    if not command:
        parser.error("no command: name it after '--'")
    root = options.root.resolve()
    base = gatelib.base_commit(root)
    if base is None:
        print("changed: green: there is no commit to compare the tree with; nothing is judged")
        return 0
    if gatelib.adoption(root, base) and not os.environ.get(BITING):
        print(
            f"changed: green: the base {base[:9]} holds no {gatelib.BINDING}, so this change is"
            " the adoption and every line is new against it; nothing is judged as changed, and"
            " tier 2 judges the whole tree"
        )
        return 0
    words = [word.replace("{base}", base) for word in command]
    try:
        done = subprocess.run(words, cwd=root, env={**os.environ, BASE_ENV: base}, check=False)
    except OSError as error:
        print(f"changed: cannot run {words[0]}: {error}")
        return 127
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
