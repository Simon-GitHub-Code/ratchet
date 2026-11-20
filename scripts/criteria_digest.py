#!/usr/bin/env python3
"""The criteria digest of a ticket or spec body, for its conjecture's `- Criteria:` line.

The digest is the git blob id of the criteria: the line that is the heading, and every line that
is not blank from it up to the next line that starts `## `, each ending in a newline, after every
carriage return is dropped. A heading the body holds twice counts both of its sections.

Prints the digest and exits 0. Prints nothing and exits 1 when no line of the body is the heading,
and 2 when the body cannot be read.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

SECTION = b"## "
BLANK = b" \t"


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


def blob_id(text: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(text) + text, usedforsecurity=False).hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="criteria_digest.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("heading", help="the criteria heading, as the body spells it")
    parser.add_argument("body", nargs="?", type=Path, help="the body's file; stdin when absent")
    options = parser.parse_args(argv[1:])
    try:
        body = options.body.read_bytes() if options.body else sys.stdin.buffer.read()
    except OSError as error:
        print(f"criteria digest: {options.body} cannot be read: {error}", file=sys.stderr)
        return 2
    text = criteria(body, os.fsencode(options.heading))
    if text is None:
        print(f"criteria digest: the body has no `{options.heading}` line", file=sys.stderr)
        return 1
    print(blob_id(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
