#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

BINARY_SNIFF = 8000
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class InvalidNumberError(argparse.ArgumentTypeError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"'{raw}' is not a positive integer")


class RenumberError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def positive_int(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit() or int(raw) <= 0:
        raise InvalidNumberError(raw)
    return int(raw)


def tag(number: int) -> str:
    return f"{number:04d}"


def parse_args(argv: Sequence[str]) -> tuple[Path, str | None, str | None, int, int]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--file")
    parser.add_argument("source", type=positive_int)
    parser.add_argument("target", type=positive_int)
    options = parser.parse_args(argv)
    if options.source == options.target:
        parser.error(f"source and target ADR numbers are both {tag(options.source)}")
    return (
        Path(options.root).resolve(),
        options.base,
        options.file,
        options.source,
        options.target,
    )


def base_adr_files(root: Path, base: str | None) -> set[str]:
    if base is None:
        return set()
    listed = gatelib.git(root, "ls-tree", "-r", "--name-only", "-z", base, "--", "docs/adr")
    return {path for path in (listed or "").split("\0") if path}


def resolve_explicit(root: Path, adr_dir: Path, *, from_tag: str, file_arg: str) -> Path:
    raw = Path(file_arg)
    candidate = raw if raw.is_absolute() else root / raw
    if (
        not candidate.is_file()
        or candidate.parent.resolve() != adr_dir.resolve()
        or not candidate.name.startswith(f"{from_tag}-")
        or not candidate.name.endswith(".md")
    ):
        message = f"--file '{file_arg}' must be an existing docs/adr/{from_tag}-*.md"
        raise RenumberError(message)
    return adr_dir / candidate.name


def resolve_source(
    root: Path, adr_dir: Path, from_tag: str, *, file_arg: str | None, base: str | None
) -> Path:
    if file_arg is not None:
        return resolve_explicit(root, adr_dir, from_tag=from_tag, file_arg=file_arg)
    matches = (
        sorted(p for p in adr_dir.glob(f"{from_tag}-*.md") if p.is_file())
        if adr_dir.is_dir()
        else []
    )
    if not matches:
        message = f"no ADR file matching docs/adr/{from_tag}-*.md"
        raise RenumberError(message)
    if len(matches) == 1:
        return matches[0]
    existing = base_adr_files(root, base)
    added = [path for path in matches if f"docs/adr/{path.name}" not in existing]
    if len(added) == 1:
        return added[0]
    candidates = ", ".join(f"docs/adr/{path.name}" for path in matches)
    message = f"multiple ADRs match {from_tag} ({candidates}); pass --file"
    raise RenumberError(message)


def move_record(root: Path, old_rel: str, new_rel: str) -> None:
    if gatelib.git(root, "ls-files", "--error-unmatch", "--", old_rel) is not None:
        gatelib.git_checked(root, "mv", "--", old_rel, new_rel)
        return
    (root / old_rel).rename(root / new_rel)


def parse_diff_lines(diff_text: str) -> dict[str, set[int]]:
    edits: dict[str, set[int]] = {}
    current: str | None = None
    old_rem = 0
    new_rem = 0
    for line in diff_text.splitlines():
        if old_rem > 0 or new_rem > 0:
            if line.startswith("-"):
                old_rem -= 1
            elif line.startswith("+"):
                new_rem -= 1
            continue
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("diff --git ") or line == "+++ /dev/null":
            current = None
        elif (match := HUNK.match(line)) is not None and current is not None:
            old_rem = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_rem = int(match.group(4)) if match.group(4) is not None else 1
            if new_rem > 0:
                edits.setdefault(current, set()).update(range(new_start, new_start + new_rem))
    return edits


def edit_scope(root: Path, base: str | None, new_rel: str) -> tuple[set[str], dict[str, set[int]]]:
    if base is None:
        return set(gatelib.seen(root)) | {new_rel}, {}
    diff_out = (
        gatelib.git(
            root,
            "-c",
            "core.quotepath=false",
            "diff",
            "-U0",
            "--no-renames",
            "--diff-filter=AMCR",
            base,
        )
        or ""
    )
    untracked = gatelib.git(root, "ls-files", "--others", "--exclude-standard", "-z") or ""
    full = {path for path in untracked.split("\0") if path} | {new_rel}
    return full, parse_diff_lines(diff_out)


def make_replacer(
    old_name: str, new_name: str, *, from_tag: str, to_tag: str
) -> Callable[[str], str]:
    file_ref = re.compile(rf"(?<!\d){re.escape(old_name)}\b")
    adr_ref = re.compile(rf"\b(ADR)(\s+|-){re.escape(from_tag)}\b")
    superseded_ref = re.compile(rf"(superseded-by:\s*){re.escape(from_tag)}\b")

    def replace_line(line: str) -> str:
        line = file_ref.sub(new_name, line)
        line = adr_ref.sub(rf"\g<1>\g<2>{to_tag}", line)
        return superseded_ref.sub(rf"\g<1>{to_tag}", line)

    return replace_line


def rewrite_file(path: Path, target_lines: set[int] | None, replacer: Callable[[str], str]) -> int:
    if path.is_symlink() or not path.is_file():
        return 0
    data = path.read_bytes()
    if b"\0" in data[:BINARY_SNIFF]:
        return 0
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return 0
    changed = 0
    rewritten: list[str] = []
    for idx, line in enumerate(text.splitlines(keepends=True), 1):
        updated = replacer(line) if target_lines is None or idx in target_lines else line
        if updated != line:
            changed += 1
        rewritten.append(updated)
    if changed:
        path.write_bytes("".join(rewritten).encode("utf-8"))
    return changed


def renumber(
    root: Path,
    source: int,
    target: int,
    *,
    base_ref: str | None = None,
    file_arg: str | None = None,
) -> tuple[str, str, list[tuple[str, int]]]:
    from_tag, to_tag = tag(source), tag(target)
    adr_dir = root / "docs" / "adr"
    base = gatelib.base_commit(root, base_ref)
    old_path = resolve_source(root, adr_dir, from_tag, file_arg=file_arg, base=base)
    existing_to = sorted(p for p in adr_dir.glob(f"{to_tag}-*.md") if p.is_file())
    if existing_to:
        message = f"target ADR {to_tag} already exists: docs/adr/{existing_to[0].name}"
        raise RenumberError(message)
    slug = old_path.name.removeprefix(f"{from_tag}-")
    new_name = f"{to_tag}-{slug}"
    old_rel, new_rel = f"docs/adr/{old_path.name}", f"docs/adr/{new_name}"
    move_record(root, old_rel, new_rel)
    full, line_edits = edit_scope(root, base, new_rel)
    replacer = make_replacer(old_path.name, new_name, from_tag=from_tag, to_tag=to_tag)
    updated: list[tuple[str, int]] = []
    for rel in sorted(full | set(line_edits)):
        if ".git" in PurePosixPath(rel).parts:
            continue
        target_lines = None if rel in full else line_edits[rel]
        count = rewrite_file(root / rel, target_lines, replacer)
        if count:
            updated.append((rel, count))
    return old_rel, new_rel, updated


def main(argv: list[str]) -> int:
    root, base_ref, file_arg, source, target = parse_args(argv[1:])
    try:
        old_rel, new_rel, updated = renumber(
            root, source, target, base_ref=base_ref, file_arg=file_arg
        )
    except RenumberError as error:
        print(f"adr_renumber: {error}", file=sys.stderr)
        return 1
    print(f"renamed {old_rel} -> {new_rel}")
    if updated:
        for rel, count in updated:
            noun = "line" if count == 1 else "lines"
            print(f"updated {rel} ({count} {noun})")
    else:
        print("no citations updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
