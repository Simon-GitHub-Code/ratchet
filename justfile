# The command contract of the ratchet plugin itself. The method says gates are gated; this is
# where that applies to the method.

set shell := ["bash", "-euo", "pipefail", "-c"]

# uv's default hard links give .venv files two names, and `claude plugin eval` refuses a plugin
# directory holding such a file.
export UV_LINK_MODE := "copy"

default:
    @just --list --unsorted

# Tier 0: shape.
tier0: lint types validate parse-workflow parse-adapters

# Tier 1: behaviour.
tier1: test coverage conformance-self-test lowered-smoke bite

check: tier0 tier1

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

test:
    uv run pytest -q hooks/tests

# The gate scripts and their library are absolute paths: every line and branch runs under test.
coverage:
    uv run coverage run -m pytest -q hooks/tests
    uv run coverage report

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
