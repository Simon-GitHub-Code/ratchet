#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import gatelib

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
