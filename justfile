# The command contract of the ratchet plugin itself. The method says gates are gated; this is
# where that applies to the method.

set shell := ["bash", "-euo", "pipefail", "-c"]

# uv's default hard links give .venv files two names, and `claude plugin eval` refuses a plugin
# directory holding such a file.
export UV_LINK_MODE := "copy"

default:
    @just --list --unsorted

# The gates of each tier, and the one runner that runs a list of them at once: a tier takes as
# long as its slowest gate, not the sum, and one run names every red gate. No two of these write
# the same file or directory; a gate that did would be declared with `--serial`.
tier0_gates := "lint types validate parse-workflow parse-adapters"
tier1_gates := "test conformance-self-test lowered-smoke bite"
parallel := "python3 scripts/parallel.py --runner '" + just_executable() + "' --report .ratchet/timing.jsonl"

# Tier 0: shape.
tier0: sync
    {{parallel}} -- {{tier0_gates}}

# Tier 1: behaviour. `test` is the suite once, under coverage, which is the coverage gate too.
tier1: sync
    {{parallel}} -- {{tier1_gates}}

check: sync
    {{parallel}} -- {{tier0_gates}} {{tier1_gates}}

# The inner loop: the same gates, stopped at the first red.
check-fast: sync
    {{parallel}} --fail-fast -- {{tier0_gates}} {{tier1_gates}}

# One environment sync before the gates start, so no two `uv run` race to build it.
sync:
    uv sync --locked

lint:
    uv run ruff check hooks scripts
    uv run ruff format --check hooks scripts

types:
    uv run mypy --strict hooks/ratchet_guards scripts

validate:
    claude plugin validate .

# The Workflow runtime runs this script as the body of an async function, where its top-level
# `return` is legal; `node --check` reads a file as a module or script and rejects it on Node 24.
# scripts/parse-js.mjs compiles it the way the runtime does. Node 22.13 or later.
parse-workflow:
    node --no-warnings scripts/parse-js.mjs workflow workflows/pipeline.js

# Types are stripped explicitly: `node --check` does not strip them on Node 24. Node 22.13 or later.
parse-adapters:
    node --no-warnings scripts/parse-js.mjs adapter adapters/pi/ratchet-guard.ts adapters/opencode/ratchet-guard.ts

# The suite once, across every core and in random order, under coverage. The gate scripts and
# their library are absolute paths: every line and branch must run under test, and
# `fail_under = 100` in pyproject.toml makes this run red when one does not.
test:
    uv run pytest -n auto --cov --cov-report=term-missing:skip-covered hooks/tests

conformance-self-test:
    python3 scripts/conformance.py --self-test

# Every shipped gate against its synthetic violation; each must go red, and green without it.
bite:
    python3 scripts/bite.py

# The lowered gate on this repository's own thresholds: no ceiling raised against the merge base.
lowered-smoke:
    python3 scripts/lowered.py

# Time one gate against its ceiling in quality/thresholds.toml; CI wraps every matrix job in it.
budget key *flags:
    python3 scripts/budget.py {{key}} --report .ratchet/timing.jsonl {{flags}} -- just {{key}}
