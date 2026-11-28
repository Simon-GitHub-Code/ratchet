#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import justfile_text

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType

DEFAULT_GRACE = 5.0
TIMEOUT_EXIT = 124
NOT_FOUND_EXIT = 127
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"})
TIMEOUT_VALUE_FLAGS = frozenset({"-k", "--kill-after", "-s", "--signal"})
WRAPPERS = frozenset({"command", "exec"})


def _split_shell_commands(line: str) -> list[str]:
    commands: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            current.append(char)
            escaped = True
        elif quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            current.append(char)
            quote = char
        elif char in {";", "|"} or line[index : index + 2] == "&&":
            commands.append("".join(current))
            current = []
            if line[index : index + 2] in {"&&", "||"}:
                index += 1
        else:
            current.append(char)
        index += 1
    commands.append("".join(current))
    return [segment.strip() for segment in commands if segment.strip()]


def _command_head(tokens: Sequence[str]) -> tuple[str, Sequence[str]] | None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if ASSIGNMENT.match(token) or token in WRAPPERS:
            index += 1
            continue
        if Path(token).name == "env":
            index += 1
            while index < len(tokens):
                flag = tokens[index]
                if flag == "--":
                    index += 1
                    break
                if flag in ENV_VALUE_FLAGS:
                    index += 2
                elif flag.startswith("-") or ASSIGNMENT.match(flag):
                    index += 1
                else:
                    break
            continue
        return token, tokens[index + 1 :]
    return None


def _missing_timeout_flags(args: Sequence[str]) -> tuple[str, ...]:
    foreground = False
    kill_after = False
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--" or not word.startswith("-") or word == "-":
            break
        if word == "--foreground":
            foreground = True
            index += 1
        elif word in {"-k", "--kill-after"}:
            kill_after = True
            index += 2
        elif word.startswith(("-k", "--kill-after=")):
            kill_after = True
            index += 1
        elif word in TIMEOUT_VALUE_FLAGS:
            index += 2
        else:
            index += 1
    missing: list[str] = []
    if not foreground:
        missing.append("--foreground")
    if not kill_after:
        missing.append("--kill-after")
    return tuple(missing)


def _recipe_lines(lines: Sequence[str]) -> list[str]:
    joined: list[str] = []
    buffer = ""
    for raw in lines:
        stripped = raw.strip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
        else:
            joined.append(buffer + stripped)
            buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def unsafe_justfile_timeouts(justfile: str) -> list[tuple[str, tuple[str, ...]]]:
    findings: list[tuple[str, tuple[str, ...]]] = []
    lines = justfile.split("\n")
    for block in justfile_text.blocks(lines):
        if block.parsed is None:
            continue
        body = justfile_text.code_of("\n".join(lines[block.head + 1 : block.end]))
        missing_for_recipe: list[str] = []
        for raw_line in _recipe_lines(body.splitlines()):
            line = raw_line.lstrip("@- ")
            for segment in _split_shell_commands(line):
                try:
                    tokens = shlex.split(segment)
                except ValueError:
                    continue
                head = _command_head(tokens)
                if head is None or Path(head[0]).name != "timeout":
                    continue
                for flag in _missing_timeout_flags(head[1]):
                    if flag not in missing_for_recipe:
                        missing_for_recipe.append(flag)
        if missing_for_recipe:
            findings.append((block.name, tuple(missing_for_recipe)))
    return findings


def run_timed(command: Sequence[str], *, seconds: float, grace: float = DEFAULT_GRACE) -> int:
    def on_signal(signum: int, _frame: FrameType | None) -> None:
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    process: subprocess.Popen[bytes] | None = None
    try:
        try:
            with gatelib.signals_held():
                process = subprocess.Popen(
                    list(command),
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as error:
            print(f"timed: cannot run {command[0]}: {error}", file=sys.stderr)
            return NOT_FOUND_EXIT
        try:
            code = process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            gatelib.stop_group(process, grace=grace)
            print(f"timed: {command[0]} timed out after {seconds:g}s", file=sys.stderr)
            return TIMEOUT_EXIT
        gatelib.end_group(process.pid)
        return 128 - code if code < 0 else code
    except BaseException:
        if process is not None:
            gatelib.stop_group(process, grace=grace)
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command in its own session with a wall-clock timeout."
    )
    parser.add_argument("seconds", type=float)
    parser.add_argument("--grace", type=float, default=DEFAULT_GRACE)
    args = argv[1:]
    if "--" in args:
        split = args.index("--")
        options = parser.parse_args(args[:split])
        command = args[split + 1 :]
    else:
        parser.add_argument("command", nargs=argparse.REMAINDER)
        options = parser.parse_args(args)
        command = options.command
    if not math.isfinite(options.seconds) or options.seconds <= 0:
        parser.error("seconds must be a positive finite number")
    if not math.isfinite(options.grace) or options.grace < 0:
        parser.error("--grace must be a non-negative finite number")
    if not command:
        parser.error("missing command after seconds")
    return run_timed(command, seconds=options.seconds, grace=options.grace)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
