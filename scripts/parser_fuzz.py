#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import gatelib

MODES = ("parser", "lister")
KINDS = frozenset({"comment", "doc"})
FILE_PLACEHOLDER = "{file}"
DEFAULT_TIMEOUT = 5.0
DEFAULT_EXTENSION = ".txt"
RECORD_FIELDS = 4

CORPUS: tuple[tuple[str, bytes], ...] = (
    ("empty", b""),
    ("utf8-bom", b"\xef\xbb\xbfalpha = 1\n"),
    ("crlf-and-cr", b"alpha = 1\r\nbeta = 2\rgamma = 3\r\n"),
    ("no-final-newline", b"alpha = 1"),
    ("unclosed-quote", b'text = "unterminated string\n'),
    ("unclosed-comment", b"/* unterminated block comment\n"),
    ("unclosed-bracket", b"items = ([{1, 2\n"),
    ("escaped-quotes", b"a = \"escaped \\\" quote \\\\\" + 'single \\' \\\\'\n"),
    ("nested-comments", b"/* outer /* inner */ still open? */\n"),
    ("null-and-invalid-utf8", b"alpha\x00beta\xff\xfe\xfd\n"),
    ("long-single-line", b"x = " + b"a" * 8192 + b"\n"),
    ("multibyte-unicode", "emoji = '\u2697' # caf\u00e9 \u2014 \u03c0\n".encode()),
)


@dataclass(frozen=True)
class Config:
    mode: str
    timeout: float
    extension: str
    command: list[str]


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog="parser-fuzz")
    parser.add_argument("--mode", choices=MODES, default="parser")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--extension", default=DEFAULT_EXTENSION)
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    if not command:
        parser.error("a command to run must follow '--'")
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    return options, command


def command_for(command: list[str], sample: Path) -> list[str]:
    target = str(sample)
    if any(FILE_PLACEHOLDER in part for part in command):
        return [part.replace(FILE_PLACEHOLDER, target) for part in command]
    return [*command, target]


def run_once(argv: list[str], timeout: float) -> subprocess.CompletedProcess[bytes] | str:
    try:
        completed = subprocess.run(argv, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout:g}s"
    except OSError as error:
        return f"cannot execute {argv[0]}: {error}"
    if completed.returncode < 0:
        return f"crashed with signal {-completed.returncode}"
    return completed


def validate_lister(name: str, data: bytes, stdout: bytes) -> list[str]:
    source_lines = data.decode("utf-8", errors="replace").splitlines()
    problems = []
    for row in stdout.decode("utf-8", errors="replace").splitlines():
        if not row.strip():
            continue
        fields = row.split("\t", RECORD_FIELDS - 1)
        if (
            len(fields) != RECORD_FIELDS
            or not fields[0]
            or not fields[1].isdecimal()
            or fields[2] not in KINDS
        ):
            problems.append(f"{name}: malformed lister record: {row[:80]!r}")
            continue
        line_no, text = int(fields[1]), fields[3]
        if not 0 < line_no <= len(source_lines):
            problems.append(f"{name}: lister line {line_no} out of range (1..{len(source_lines)})")
            continue
        if text not in source_lines[line_no - 1]:
            problems.append(f"{name}: lister text {text!r} not found on line {line_no}")
    return problems


def check_case(name: str, data: bytes, config: Config) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / f"case_{name}{config.extension}"
        sample.write_bytes(data)
        argv = command_for(config.command, sample)
        first = run_once(argv, config.timeout)
        if isinstance(first, str):
            return [f"{name}: {first}"]
        second = run_once(argv, config.timeout)
        if isinstance(second, str):
            return [f"{name}: second run {second}"]
        if (second.returncode, second.stdout) != (first.returncode, first.stdout):
            return [f"{name}: non-deterministic result across repeated runs"]
        if config.mode == "lister" and first.returncode == 0:
            return validate_lister(name, data, first.stdout)
        return []


def fuzz(config: Config) -> list[str]:
    return [problem for name, data in CORPUS for problem in check_case(name, data, config)]


def main(argv: list[str]) -> int:
    options, command = parse(argv)
    config = Config(
        mode=options.mode,
        timeout=options.timeout,
        extension=options.extension,
        command=command,
    )
    return gatelib.report("parser-fuzz", fuzz(config))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
