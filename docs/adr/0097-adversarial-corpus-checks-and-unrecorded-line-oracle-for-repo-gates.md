---
status: accepted
---
# Adversarial corpus checks and unrecorded-line oracle for repo gates

Builds on ADR 0025 ([`0025-a-first-class-concern-is-held-by-a-check-that-names-no-tool.md`](0025-a-first-class-concern-is-held-by-a-check-that-names-no-tool.md)) and ADR 0063 ([`0063-every-comment-is-read-by-a-tool-and-doc-comments-cover-a-librarys-interface.md`](0063-every-comment-is-read-by-a-tool-and-doc-comments-cover-a-librarys-interface.md)).

**What was asked.** Custom repository-local parsers and gate tools (such as comment listers bound to `comments-list`, dependency manifest readers, or coverage report generators) can pass 100% unit-test coverage and happy-path bite proofs while hiding two classes of soundness defects:

- **Lexer and parser edge-case failures.** Hand-rolled scanners can crash, hang, behave non-deterministically, or emit invalid output on unclosed quotes or block comments, escaped delimiters, CRLF or CR-only line endings, UTF-8 byte-order marks, missing final newlines, nested block comments, null or invalid UTF-8 bytes, long single lines, and multi-byte Unicode text.
- **Unrecorded executable source files and lines.** A coverage report generator can silently drop an executable file or emit a file entry with zero recorded lines, letting an `absolute = true` coverage group pass at 100% when real code was never measured.

## Decision

**1. Shipped adversarial corpus runner (`scripts/parser_fuzz.py`).**

- `scripts/parser_fuzz.py` accepts `--mode` (`parser` or `lister`, default `parser`), `--timeout` (seconds, default `5.0`), `--extension` (default `.txt`), and `-- <command...>` (substituting `{file}` in arguments or appending the temporary input path).
- It writes a deterministic built-in corpus (`CORPUS`) of adversarial byte sequences into a fresh temporary directory per case, covering empty input, UTF-8 BOM, CRLF and CR-only line endings, missing final newline, unclosed quotes, unclosed block comments, unclosed brackets, escaped quotes and backslashes, nested block comments, null and invalid UTF-8 bytes, long single lines, and multi-byte Unicode.
- Each case is run twice to verify determinism and fails red (`exit 1`) on signal termination (`returncode < 0`), command execution failure, timeout (`subprocess.TimeoutExpired`), or differing exit code or stdout across runs. Deterministic clean non-zero exits (such as rejecting invalid UTF-8 bytes) are permitted.
- In `--mode lister`, when a case exits `0`, every non-empty stdout line is validated as a 4-field tab-separated record (`path\tline\tkind\ttext`) with a non-empty path, integer line in `1..line_count`, `kind` in `{"comment", "doc"}`, and `text` present on the named source line.

**2. Opt-in unrecorded-line coverage oracle (`scripts/coverage_groups.py --check-unrecorded`).**

- `scripts/coverage_groups.py` gains an opt-in `--check-unrecorded` flag, keeping default CLI behaviour without the flag unchanged.
- When `--check-unrecorded` is set, every tracked file matching an `absolute = true` coverage group (excluding non-code suffixes in `EXCLUDED_FROM_SCAN`, binary files, and empty or whitespace-only files) must appear in the merged coverage reports with `lines > 0`.
- Missing files and zero-line entries (`lines == 0`) emit a failure naming the file and group (`<path> in <group.key> has no recorded executable lines in the coverage report`).

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A parser or comment lister that terminates by signal, times out, returns non-deterministic exit codes or stdout across repeated runs, or (in `--mode lister`) emits malformed records, out-of-range line numbers, invalid kinds, or text absent from the named line passes `scripts/parser_fuzz.py` with exit `0`.
- A parser or comment lister that rejects invalid bytes with a deterministic non-zero exit code and no signal crash fails `scripts/parser_fuzz.py`.
- A tracked non-empty source file in an `absolute = true` coverage group is omitted from a Cobertura or LCOV report or recorded with zero executable lines, and `scripts/coverage_groups.py --check-unrecorded` exits `0`.
- `scripts/coverage_groups.py --check-unrecorded` rejects empty, whitespace-only, binary, or `EXCLUDED_FROM_SCAN` files, or running `scripts/coverage_groups.py` without `--check-unrecorded` alters existing gate verdicts.

## Considered options

- **Require each repository to write its own random input generator.** Rejected: every repository would duplicate the same boundary inputs (BOM, CRLF, invalid UTF-8, unclosed delimiters) and non-deterministic random seeds would weaken repeatable tier-1 gates.
- **Inspect runtime-specific error trace strings in stderr.** Rejected: matching language-specific error messages violates stack neutrality and breaks parsers that emit diagnostic text before a clean non-zero exit.
- **Enable unrecorded-line checks unconditionally in `scripts/coverage_groups.py`.** Rejected: existing repositories and fixtures can pass partial or multi-stage reports without every group file present; an opt-in `--check-unrecorded` flag protects backward compatibility while giving full-suite gates an exact oracle.

## Consequences

- `scripts/parser_fuzz.py` ships as an executable standard-library gate reporting through `gatelib.report("parser-fuzz", ...)`.
- `scripts/coverage_groups.py` adds `--check-unrecorded` and `unrecorded(...)` for `absolute = true` groups.
- `scripts/bite.py` adds bite scenarios proving both `parser-fuzz` and `coverage --check-unrecorded` catch planted faults.
