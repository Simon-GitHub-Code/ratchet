#!/usr/bin/env python3
"""The criteria digest of a ticket or spec body, for its conjecture's `- Criteria:` line.

The digest is what `git hash-object --stdin`, run in the current directory, prints for the
criteria: the line that is the heading, and every line that is not blank from it up to the next
line that starts `## `, each ending in a newline, after every carriage return is dropped. A heading
the body holds twice counts both of its sections.

Prints the digest and exits 0. Prints nothing and exits 3 when no line of the body is the heading.
Prints nothing and exits 2 when it could not run: the body cannot be read or is empty, or git
cannot be run or fails.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SECTION = b"## "
BLANK = b" \t"
DID_NOT_RUN = 2
NO_HEADING = 3


def criteria(body: bytes, heading: bytes) -> bytes | None:
    kept: list[bytes] = []
    inside = False
    found = False
    for line in body.replace(b"\r", b"").split(b"\n"):
        if line.startswith(SECTION):
            inside = line == heading
            found = found or inside
        if inside and line.strip(BLANK):
            kept.append(line + b"\n")
    return b"".join(kept) if found else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="criteria_digest.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("heading", help="the criteria heading, as the body spells it")
    parser.add_argument("body", nargs="?", type=Path, help="the body's file; stdin when absent")
    options = parser.parse_args(argv[1:])
    source = options.body or "stdin"
    try:
        body = options.body.read_bytes() if options.body else sys.stdin.buffer.read()
    except OSError as error:
        print(f"criteria digest: {source} cannot be read: {error}", file=sys.stderr)
        return DID_NOT_RUN
    if not body:
        print(f"criteria digest: the body on {source} is empty", file=sys.stderr)
        return DID_NOT_RUN
    text = criteria(body, os.fsencode(options.heading))
    if text is None:
        print(f"criteria digest: the body has no `{options.heading}` line", file=sys.stderr)
        return NO_HEADING
    try:
        hashed = subprocess.run(
            ["git", "hash-object", "--stdin"],
            input=text,
            stdout=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        print(f"criteria digest: git cannot be run: {error}", file=sys.stderr)
        return DID_NOT_RUN
    if hashed.returncode != 0:
        print(f"criteria digest: git hash-object exited {hashed.returncode}", file=sys.stderr)
        return DID_NOT_RUN
    sys.stdout.buffer.write(hashed.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
