# The command contract of the ratchet plugin itself. The method says gates are gated; this is
# where that applies to the method.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list --unsorted

# Tier 0: shape.
tier0: lint types validate parse-workflow

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

parse-workflow:
    node --check workflows/feature.js

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
